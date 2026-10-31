# pyrefly: ignore [missing-import]
import os
import re
from datetime import datetime

import cups

from src.logger import logger

PRINTER_STATE_MAP = {3: "idle", 4: "printing", 5: "stopped"}


class CupsBackend:

    # ── Printer management ───────────────────────────────────────────

    def get_printers(self):
        conn = cups.Connection()
        if not conn:
            return {}
        try:
            return conn.getPrinters()
        except Exception:
            logger.exception("Failed to fetch printers.")
            return {}

    def add_printer(self, name, uri, model_name=None, ppd_name=None):
        if not ppd_name:
            ppd_name = self.find_best_ppd(model_name, device_uri=uri)
        conn = cups.Connection()
        if not conn:
            return False, "No CUPS connection"
        try:
            conn.addPrinter(name, device=uri, ppdname=ppd_name)
            conn.enablePrinter(name)
            conn.acceptJobs(name)
            logger.info(f"Printer '{name}' added.")
            return True, None
        except cups.IPPError as e:
            err_msg = f"CUPS IPP Error ({e.args[0]}): {e.args[1]}"
            logger.error(f"Failed to add printer '{name}': {err_msg}")
            return False, err_msg
        except Exception as e:
            logger.exception(f"Failed to add printer '{name}': {e}")
            return False, str(e)

    def delete_printer(self, printer_name):
        conn = cups.Connection()
        if not conn:
            return False
        try:
            conn.deletePrinter(printer_name)
            return True
        except Exception as e:
            logger.error(f"Failed to delete printer '{printer_name}': {e}")
            return False

    def pause_printer(self, printer_name):
        conn = cups.Connection()
        if not conn:
            return False
        try:
            conn.disablePrinter(printer_name)
            return True
        except Exception as e:
            logger.error(f"Failed to pause printer '{printer_name}': {e}")
            return False

    def resume_printer(self, printer_name):
        conn = cups.Connection()
        if not conn:
            return False
        try:
            conn.enablePrinter(printer_name)
            return True
        except Exception as e:
            logger.error(f"Failed to resume printer '{printer_name}': {e}")
            return False

    def print_test_page(self, printer_name):
        conn = cups.Connection()
        if not conn:
            return False
        test_file = "/usr/share/cups/data/testprint"
        if not os.path.exists(test_file):
            test_file = "/tmp/pardus_test_page.txt"
            with open(test_file, "w") as f:
                f.write("Pardus Printer Test Page\n")
        try:
            job_id = conn.printFile(printer_name, test_file, "Test Page", {})
            return job_id > 0
        except Exception as e:
            logger.error(f"Failed to print test page on '{printer_name}': {e}")
            return False

    # ── Default printer ──────────────────────────────────────────────

    def get_default_printer(self):
        conn = cups.Connection()
        if not conn:
            return None
        try:
            return conn.getDefault()
        except Exception as e:
            logger.error(f"Failed to get default printer: {e}")
            return None

    def set_default_printer(self, printer_name):
        conn = cups.Connection()
        if not conn:
            return False
        try:
            conn.setDefault(printer_name)
            return True
        except Exception as e:
            logger.error(f"Failed to set default printer '{printer_name}': {e}")
            return False

    # ── Print jobs ───────────────────────────────────────────────────

    def get_print_jobs(self, printer_name=None):
        conn = cups.Connection()
        if not conn:
            return []
        try:
            jobs = conn.getJobs(
                my_jobs=False,
                which_jobs="not-completed",
                requested_attributes=[
                    "job-id", "job-name", "job-state", "job-printer-uri",
                    "job-originating-user-name", "job-owner",
                    "job-k-octets", "time-at-creation",
                ],
            )
        except Exception as e:
            logger.error(f"Failed to fetch print jobs: {e}")
            return []

        result = []
        for job_id, details in jobs.items():
            job_printer = details.get("job-printer-uri", "").rsplit("/", 1)[-1]
            if printer_name and job_printer != printer_name:
                continue

            user = (details.get("job-originating-user-name")
                    or details.get("job-owner")
                    or details.get("user")
                    or "Unknown")

            raw_time = details.get("time-at-creation") or details.get("time", 0)
            if isinstance(raw_time, (int, float)) and raw_time:
                formatted_time = datetime.fromtimestamp(raw_time).strftime("%Y-%m-%d %H:%M:%S")
            else:
                formatted_time = str(raw_time)

            raw_size = details.get("job-k-octets") or details.get("size", 0)
            formatted_size = f"{raw_size} KB" if isinstance(raw_size, (int, float)) else str(raw_size)

            result.append({
                "job_id": str(job_id),
                "printer": job_printer,
                "title": details.get("job-name", details.get("title", "Unknown")),
                "user": user,
                "size": formatted_size,
                "state": details.get("job-state", details.get("state", 0)),
                "time": formatted_time,
            })
        return result

    def cancel_job(self, job_id):
        conn = cups.Connection()
        if not conn:
            return False
        try:
            if isinstance(job_id, str):
                numeric_part = "".join(filter(str.isdigit, job_id))
                jid = int(numeric_part) if numeric_part else int(job_id)
            else:
                jid = int(job_id)
            conn.cancelJob(jid)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
            return False

    # ── PPD / driver matching ────────────────────────────────────────

    def get_ppds(self):
        conn = cups.Connection()
        if not conn:
            return {}
        try:
            return conn.getPPDs()
        except Exception as e:
            logger.error(f"Failed to fetch PPDs: {e}")
            return {}

    def find_best_ppd(self, model_name, device_uri=""):
        default_ppd = "drv:///sample.drv/generic.ppd"
        if not model_name:
            return default_ppd

        ppds = self.get_ppds()
        if not ppds:
            return default_ppd

        def normalize(text):
            return re.sub(r"[^a-z0-9]", "", text.lower())

        target_raw = model_name.strip().lower()
        target_clean = normalize(model_name)

        for ppd_key, ppd_info in ppds.items():
            make_model = ppd_info.get("ppd-make-and-model", "").strip().lower()
            if target_raw == make_model or normalize(make_model) == target_clean:
                return ppd_key

        noise_words = {"series", "driver", "cups", "printer", "hpcups", "brlaser", "pcl"}
        target_words = set(re.findall(r"[a-z0-9]+", target_raw)) - noise_words

        best_ppd, best_score = None, 0
        for ppd_key, ppd_info in ppds.items():
            make_model = ppd_info.get("ppd-make-and-model", "").strip().lower()
            make_clean = normalize(make_model)

            if (len(target_clean) > 4 and target_clean in make_clean) or \
               (len(make_clean) > 4 and make_clean in target_clean):
                return ppd_key

            ppd_words = set(re.findall(r"[a-z0-9]+", make_model)) - noise_words
            score = len(target_words & ppd_words)
            if score > best_score:
                best_score = score
                best_ppd = ppd_key

        if best_ppd and best_score >= 2:
            return best_ppd

        if device_uri and device_uri.startswith(("ipp://", "ipps://", "http://", "https://")):
            return "everywhere"

        return default_ppd

    # ── Device discovery ─────────────────────────────────────────────

    def discover_devices(self):
        try:
            conn = cups.Connection()
            return conn.getDevices()
        except Exception as e:
            logger.error(f"Failed to discover devices: {e}")
            return {}

    def get_printer_attributes_by_uri(self, uri):
        if not uri or not uri.startswith(("ipp://", "ipps://", "http://", "https://")):
            return {}
        conn = cups.Connection()
        if not conn:
            return {}
        try:
            attrs = conn.getPrinterAttributes(uri=uri)
            info = attrs.get("printer-info", "")
            model = attrs.get("printer-make-and-model", "")
            return {"suggested_name": info or model, "make_and_model": model}
        except Exception as e:
            logger.debug(f"Could not fetch attributes for '{uri}': {e}")
            return {}

    @staticmethod
    def get_printer_status_from_attrs(printer_attrs):
        state = printer_attrs.get("printer-state", 0)
        return PRINTER_STATE_MAP.get(state, "unknown")
