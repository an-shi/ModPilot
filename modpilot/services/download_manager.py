from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ..config import AppPaths, ConfigStore
from ..core.errors import (
    DownloadCancelled,
    DownloadError,
    NexusAuthError,
    NexusPremiumRequired,
    NexusRateLimitError,
)
from ..core.paths import sanitize_filename
from ..data.db import Database
from ..data.models import Download
from ..data.repositories import DownloadRepository, GameRepository
from ..domain.enums import DownloadKind, DownloadStatus
from ..domain.models import DownloadView
from ..version import USER_AGENT
from .activity import ActivityService
from .nexus.client import NexusClient
from ..i18n import tr

log = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024


@dataclass
class DownloadEvent:
    download_id: int
    status: str
    progress: int
    downloaded_bytes: int
    total_bytes: int
    error: str | None
    archive_path: str | None
    kind: str
    target_mod_id: int | None


@dataclass
class _JobControl:
    pause: threading.Event
    cancel: threading.Event


class DownloadManager:
    def __init__(
        self,
        db: Database,
        paths: AppPaths,
        config: ConfigStore,
        nexus: NexusClient,
        activity: ActivityService,
        on_event: Callable[[DownloadEvent], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.db = db
        self.paths = paths
        self.config = config
        self.nexus = nexus
        self.activity = activity
        self.on_event = on_event
        self._transport = transport
        self._jobs: dict[int, _JobControl] = {}
        self._lock = threading.Lock()
        self._shutdown = False
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._workers = [
            threading.Thread(
                target=self._worker_loop,
                name=f"modpilot-download-{index + 1}",
                daemon=True,
            )
            for index in range(2)
        ]
        for worker in self._workers:
            worker.start()

    def reset_interrupted(self) -> int:
        with self.db.session() as session:
            count = DownloadRepository(session).mark_interrupted()
        if count:
            log.info(tr('services.download_manager.marked_d_interrupted_downloads_as_paused'), count)
        return count

    def _emit(self, row: Download) -> None:
        if not self.on_event:
            return
        event = DownloadEvent(
            download_id=row.id,
            status=row.status,
            progress=row.progress,
            downloaded_bytes=row.downloaded_bytes,
            total_bytes=row.total_bytes,
            error=row.error,
            archive_path=row.archive_path,
            kind=row.kind,
            target_mod_id=row.target_mod_id,
        )
        try:
            self.on_event(event)
        except Exception:
            log.exception(tr('services.download_manager.download_event_callback_failed'))

    def enqueue(
        self,
        *,
        game_id: int,
        nexus_mod_id: int,
        file_id: int,
        mod_name: str,
        file_name: str,
        expected_md5: str = "",
        kind: DownloadKind | str = DownloadKind.MOD,
        target_mod_id: int | None = None,
    ) -> DownloadView:
        if not self.nexus.is_configured():
            raise NexusAuthError(tr('services.download_manager.nexus_api_key_is_not_configured'))
        safe_name = sanitize_filename(file_name or f"{mod_name}-{file_id}.zip", fallback=f"mod-{file_id}.zip")
        if "." not in safe_name:
            safe_name += ".zip"
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise DownloadError(tr('services.download_manager.no_game_has_been_added'))
            row = DownloadRepository(session).add(
                game_id=game_id,
                nexus_mod_id=nexus_mod_id,
                file_id=file_id,
                mod_name=mod_name,
                file_name=safe_name,
                status=DownloadStatus.QUEUED.value,
                kind=str(kind),
                target_mod_id=target_mod_id,
                expected_md5=expected_md5 or None,
            )
            download_id = row.id
        self._start(download_id)
        return self.get(download_id)

    def get(self, download_id: int) -> DownloadView:
        with self.db.session() as session:
            row = DownloadRepository(session).get(download_id)
            if row is None:
                raise DownloadError(tr('services.download_manager.download_task_does_not_exist'))
            return self._to_view(row)

    def list_for_game(self, game_id: int) -> list[DownloadView]:
        with self.db.session() as session:
            rows = DownloadRepository(session).list_for_game(game_id)
            return [self._to_view(row) for row in rows]

    def pending_path(self, download_id: int) -> Path | None:
        with self.db.session() as session:
            row = DownloadRepository(session).get(download_id)
            if row is None:
                return None
            return self._paths_for(row)[1]

    def pause(self, download_id: int) -> None:
        with self._lock:
            job = self._jobs.get(download_id)
        if job is not None:
            job.pause.set()
        with self.db.session() as session:
            repo = DownloadRepository(session)
            row = repo.get(download_id)
            if row is None:
                raise DownloadError(tr('services.download_manager.download_task_does_not_exist'))
            if row.status in (DownloadStatus.QUEUED.value, DownloadStatus.DOWNLOADING.value):
                repo.update(row, status=DownloadStatus.PAUSED.value)
                self._emit(row)

    def resume(self, download_id: int) -> None:
        with self.db.session() as session:
            repo = DownloadRepository(session)
            row = repo.get(download_id)
            if row is None:
                raise DownloadError(tr('services.download_manager.download_task_does_not_exist'))
            if row.status not in (DownloadStatus.PAUSED.value, DownloadStatus.FAILED.value, DownloadStatus.CANCELLED.value):
                return
            repo.update(
                row,
                status=DownloadStatus.QUEUED.value,
                error=None,
                completed_at=None,
            )
        with self._lock:
            job = self._jobs.get(download_id)
        if job is not None:
            job.cancel.clear()
            job.pause.clear()
            with self.db.session() as session:
                repo = DownloadRepository(session)
                row = repo.get(download_id)
                if row:
                    repo.update(row, status=DownloadStatus.DOWNLOADING.value)
                    self._emit(row)
        else:
            self._start(download_id)

    def cancel(self, download_id: int) -> None:
        with self._lock:
            job = self._jobs.get(download_id)
        if job is not None:
            job.cancel.set()
            job.pause.clear()
        with self.db.session() as session:
            repo = DownloadRepository(session)
            row = repo.get(download_id)
            if row is None:
                raise DownloadError(tr('services.download_manager.download_task_does_not_exist'))
            if row.status not in (
                DownloadStatus.COMPLETED.value,
                DownloadStatus.CANCELLED.value,
            ):
                repo.update(
                    row,
                    status=DownloadStatus.CANCELLED.value,
                    error=tr('services.download_manager.download_canceled_by_the_user'),
                )
                self._emit(row)
            _final, part = self._paths_for(row)
            if part and part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass

    def retry(self, download_id: int) -> None:
        with self.db.session() as session:
            repo = DownloadRepository(session)
            row = repo.get(download_id)
            if row is None:
                raise DownloadError(tr('services.download_manager.download_task_does_not_exist'))
            repo.update(
                row,
                status=DownloadStatus.QUEUED.value,
                error=None,
                retries=0,
                completed_at=None,
            )
        with self._lock:
            active = download_id in self._jobs
        if not active:
            self._start(download_id)

    def _start(self, download_id: int) -> None:
        if self._shutdown:
            return
        with self._lock:
            if download_id in self._jobs:
                return
            job = _JobControl(pause=threading.Event(), cancel=threading.Event())
            self._jobs[download_id] = job
        self._queue.put(download_id)

    def _worker_loop(self) -> None:
        while True:
            try:
                download_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._shutdown:
                    return
                continue
            try:
                if download_id is None:
                    return
                with self._lock:
                    job = self._jobs.get(download_id)
                if job is None:
                    continue
                self._run(download_id, job)
            except Exception:
                log.exception(tr('services.download_manager.download_worker_thread_error'))
            finally:
                self._queue.task_done()

    def _to_view(self, row: Download) -> DownloadView:
        return DownloadView(
            id=row.id,
            game_id=row.game_id,
            nexus_mod_id=row.nexus_mod_id,
            file_id=row.file_id,
            mod_name=row.mod_name,
            file_name=row.file_name,
            archive_path=row.archive_path,
            status=DownloadStatus(row.status)
            if row.status in DownloadStatus._value2member_map_
            else DownloadStatus.FAILED,
            progress=row.progress,
            downloaded_bytes=row.downloaded_bytes,
            total_bytes=row.total_bytes,
            error=row.error,
            kind=DownloadKind(row.kind)
            if row.kind in DownloadKind._value2member_map_
            else DownloadKind.MOD,
            target_mod_id=row.target_mod_id,
            retries=row.retries,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
        )

    def _paths_for(self, row: Download) -> tuple[Path, Path]:
        downloads_root = self.paths.resolve_downloads(self.config.config.download_dir)
        target_dir = downloads_root / f"game_{row.game_id}" / f"mod_{row.nexus_mod_id or 'unknown'}"
        filename = sanitize_filename(row.file_name or f"file-{row.file_id}.zip", fallback="download.zip")
        if "." not in filename:
            filename += ".zip"
        final = target_dir / filename
        part = final.with_name(final.name + ".part")
        return final, part

    def _update_db(self, download_id: int, **fields) -> Download | None:  # type: ignore[no-untyped-def]
        with self.db.session() as session:
            repo = DownloadRepository(session)
            row = repo.get(download_id)
            if row is None:
                return None
            repo.update(row, **fields)
            session.flush()
            session.expunge(row)
            return row

    def _run(self, download_id: int, job: _JobControl) -> None:
        try:
            while job.pause.is_set() and not job.cancel.is_set():
                time.sleep(0.15)
            if job.cancel.is_set():
                raise DownloadCancelled(tr('services.download_manager.download_canceled_by_the_user'))
            row = self._update_db(
                download_id,
                status=DownloadStatus.DOWNLOADING.value,
                error=None,
            )
            if row:
                self._emit(row)
            last_error: Exception | None = None
            for attempt in range(3):
                if job.cancel.is_set():
                    raise DownloadCancelled(tr('services.download_manager.download_canceled_by_the_user'))
                if attempt:
                    row = self._update_db(download_id, retries=attempt)
                    if row:
                        self._emit(row)
                    for _ in range(20):
                        if job.cancel.is_set():
                            raise DownloadCancelled(tr('services.download_manager.download_canceled_by_the_user'))
                        time.sleep(0.2)
                try:
                    self._download(download_id, job)
                    return
                except (NexusAuthError, NexusPremiumRequired, NexusRateLimitError, DownloadCancelled):
                    raise
                except Exception as exc:
                    last_error = exc
                    log.warning(tr('services.download_manager.failed_to_download_s_attempt_d_s'), download_id, attempt + 1, exc)
                    if attempt < 2:
                        continue
                    raise
            if last_error:
                raise last_error
        except DownloadCancelled:
            if self._shutdown:
                row = self._update_db(
                    download_id,
                    status=DownloadStatus.PAUSED.value,
                    error=tr('services.download_manager.the_app_was_closed_so_downloads_have_been_paused'),
                )
            else:
                row = self._update_db(
                    download_id,
                    status=DownloadStatus.CANCELLED.value,
                    error=tr('services.download_manager.download_canceled_by_the_user'),
                )
            if row:
                self._emit(row)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            log.error(tr('services.download_manager.download_task_s_failed_s'), download_id, message)
            row = self._update_db(
                download_id,
                status=DownloadStatus.PAUSED.value if self._shutdown else DownloadStatus.FAILED.value,
                error=message,
            )
            if row:
                self._emit(row)
                if not self._shutdown:
                    self.activity.record(
                        tr('services.download_manager.download_failed_0', row.mod_name),
                        kind="download",
                        level="error",
                        game_id=row.game_id,
                        details={"download_id": download_id, "error": message},
                    )
        finally:
            with self._lock:
                self._jobs.pop(download_id, None)
            with self.db.session() as session:
                row = DownloadRepository(session).get(download_id)
                if row and row.status == DownloadStatus.COMPLETED.value:
                    self.activity.record(
                        tr('services.download_manager.download_complete_0_1', row.mod_name, row.file_name),
                        kind="download",
                        game_id=row.game_id,
                        details={"download_id": download_id, "archive": row.archive_path},
                    )

    def _download(self, download_id: int, job: _JobControl) -> None:
        with self.db.session() as session:
            repo = DownloadRepository(session)
            row = repo.get(download_id)
            if row is None:
                raise DownloadError(tr('services.download_manager.download_task_does_not_exist'))
            game = GameRepository(session).get(row.game_id)
            if game is None:
                raise DownloadError(tr('services.download_manager.no_game_has_been_added'))
            nexus_mod_id = row.nexus_mod_id
            file_id = row.file_id
            expected_md5 = row.expected_md5
            domain = game.nexus_domain
            final_path, part_path = self._paths_for(row)
        if not nexus_mod_id or not file_id:
            raise DownloadError(tr('services.download_manager.download_task_is_missing_the_nexus_mod_file_id'))
        if not domain:
            raise DownloadError(tr('services.download_manager.the_game_does_not_have_a_nexus_game_id_configured'))
        final_path.parent.mkdir(parents=True, exist_ok=True)
        links = self.nexus.get_download_links(domain, nexus_mod_id, file_id)
        url = links[0]
        offset = part_path.stat().st_size if part_path.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if offset:
            headers["Range"] = f"bytes={offset}-"

        total = 0
        downloaded = offset
        last_report = 0.0
        with httpx.Client(
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            transport=self._transport,
        ) as client:
            try:
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 401 or response.status_code == 403:
                        raise NexusPremiumRequired(
                            tr('services.download_manager.nexus_refused_the_download_check_your_account_permis_768b83')
                        )
                    if response.status_code == 404:
                        raise DownloadError(tr('services.download_manager.the_download_link_has_expired_please_try_again'))
                    if response.status_code not in (200, 206):
                        raise DownloadError(tr('services.download_manager.download_failed_http_0', response.status_code))
                    if response.status_code == 200 and offset:
                        # Server ignored Range: restart from zero.
                        offset = 0
                        downloaded = 0
                        mode = "wb"
                    else:
                        mode = "ab" if offset else "wb"
                    content_length = int(response.headers.get("Content-Length") or 0)
                    content_range = response.headers.get("Content-Range") or ""
                    if "/" in content_range:
                        try:
                            total = int(content_range.rsplit("/", 1)[1])
                        except ValueError:
                            total = 0
                    if not total and content_length:
                        total = content_length + offset
                    row = self._update_db(
                        download_id,
                        total_bytes=total,
                        downloaded_bytes=downloaded,
                        progress=int(downloaded / total * 100) if total else 0,
                    )
                    if row:
                        self._emit(row)

                    with part_path.open(mode) as handle:
                        for chunk in response.iter_bytes(CHUNK_SIZE):
                            if job.cancel.is_set():
                                raise DownloadCancelled(tr('services.download_manager.download_canceled_by_the_user'))
                            if job.pause.is_set():
                                handle.flush()
                                row = self._update_db(
                                    download_id,
                                    status=DownloadStatus.PAUSED.value,
                                    downloaded_bytes=downloaded,
                                    total_bytes=total,
                                    progress=int(downloaded / total * 100) if total else 0,
                                )
                                if row:
                                    self._emit(row)
                                while job.pause.is_set() and not job.cancel.is_set():
                                    time.sleep(0.2)
                                if job.cancel.is_set():
                                    raise DownloadCancelled(tr('services.download_manager.download_canceled_by_the_user'))
                                row = self._update_db(
                                    download_id,
                                    status=DownloadStatus.DOWNLOADING.value,
                                )
                                if row:
                                    self._emit(row)
                            if not chunk:
                                continue
                            handle.write(chunk)
                            downloaded += len(chunk)
                            now = time.monotonic()
                            if now - last_report >= 0.5 or (total and downloaded >= total):
                                last_report = now
                                progress = int(downloaded / total * 100) if total else 0
                                row = self._update_db(
                                    download_id,
                                    downloaded_bytes=downloaded,
                                    total_bytes=total,
                                    progress=min(progress, 99),
                                )
                                if row:
                                    self._emit(row)
            except httpx.HTTPError as exc:
                raise DownloadError(tr('services.download_manager.download_connection_interrupted_0', exc)) from exc

        if total and downloaded < total:
            raise DownloadError(tr('services.download_manager.download_incomplete_0_1_bytes_retry_to_resume', downloaded, total))
        if not part_path.exists():
            raise DownloadError(tr('services.download_manager.downloaded_file_does_not_exist'))
        if expected_md5:
            digest = hashlib.md5()
            with part_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual.lower() != expected_md5.lower():
                part_path.unlink(missing_ok=True)
                raise DownloadError(
                    tr('services.download_manager.file_verification_failed_md5_mismatch_the_corrupted_0300a1', expected_md5, actual)
                )
        os.replace(part_path, final_path)
        row = self._update_db(
            download_id,
            status=DownloadStatus.COMPLETED.value,
            progress=100,
            downloaded_bytes=downloaded,
            total_bytes=total or downloaded,
            archive_path=str(final_path),
            error=None,
            completed_at=datetime.now(UTC).replace(tzinfo=None),
        )
        if row:
            self._emit(row)

    def shutdown(self, wait: bool = False) -> None:
        self._shutdown = True
        with self._lock:
            for job in self._jobs.values():
                job.cancel.set()
                job.pause.clear()
        with self.db.session() as session:
            repo = DownloadRepository(session)
            for row in repo.list_active():
                if row.status in (DownloadStatus.QUEUED.value, DownloadStatus.DOWNLOADING.value):
                    repo.update(
                        row,
                        status=DownloadStatus.PAUSED.value,
                        error=tr('services.download_manager.the_app_was_closed_so_downloads_have_been_paused'),
                    )
        for _worker in self._workers:
            self._queue.put(None)
        if wait:
            for worker in self._workers:
                worker.join(timeout=3.0)
