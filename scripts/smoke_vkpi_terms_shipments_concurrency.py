#!/usr/bin/env python3
"""Smoke test for V-KPI terms/deliverables/shipments APIs plus multi-user congestion.

Seeds multiple operator-owned projects, then concurrently records terms,
deliverables, shipments, receive events, and list reads. Verifies scope denial,
financial hiding for operators, business audit rows, and marker cleanup.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema

BASE = os.environ.get("VKPI_SMOKE_BASE", "http://127.0.0.1:8102")
PREFIX = "vkpi-terms-ship-concurrency-smoke-"
WORKERS = int(os.environ.get("VKPI_CONCURRENCY_USERS", "8"))
REQUEST_TIMEOUT_SEC = float(os.environ.get("VKPI_CONCURRENCY_REQUEST_TIMEOUT_SEC", "90"))
TOTAL_TIMEOUT_SEC = float(os.environ.get("VKPI_CONCURRENCY_TOTAL_TIMEOUT_SEC", str(max(180, WORKERS * 2))))


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.actors: list[dict[str, Any]] = []
        self.outsider: dict[str, Any] = {}

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, *, token: str, expected_status: int = 200) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8")
                if resp.status != expected_status:
                    raise RuntimeError(f"expected HTTP {expected_status}, got {resp.status} for {method} {path}")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == expected_status:
                return {"status": exc.code, "body": body}
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:800]}") from exc

    def _create_actor(self, idx: int, *, suffix: str = "operator") -> dict[str, Any]:
        c = self.conn
        email = f"{self.marker}-{suffix}-{idx}@viltrox.com"
        c.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", f"{self.marker}-{suffix}-{idx}", "approved", "operator", 1, f"https://avatar.example/{self.marker}-{suffix}-{idx}.png"),
        )
        user_id = int(c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in c.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [user_id, "operator", _json({"vkpi": "write"}), 0, 1, None, self.now]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(0)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        placeholders = ",".join("?" for _ in insert_cols)
        c.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})", values)
        staff_id = int(c.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
        return {"user_id": user_id, "staff_id": staff_id, "token": make_token(user_id, "operator"), "email": email}

    def seed(self) -> None:
        c = self.conn
        self.outsider = self._create_actor(999, suffix="outsider")
        for idx in range(WORKERS):
            actor = self._create_actor(idx)
            c.execute(
                "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"{self.marker}-kol-{idx}", f"https://instagram.com/{self.marker}-{idx}", "instagram", f"{self.marker}-{idx}@creator.test", actor["staff_id"], actor["staff_id"], self.now, self.now),
            )
            kol_id = int(c.execute("SELECT id FROM kols WHERE channel_name=?", (f"{self.marker}-kol-{idx}",)).fetchone()["id"])
            project_uid = f"{self.marker}-project-{idx}"
            c.execute(
                """
                INSERT INTO vkpi_projects (
                    project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                    product_sku, product_name, platform, stage, stage_status, started_at,
                    last_activity_at, metadata_json, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    project_uid,
                    f"{self.marker} concurrency project {idx}",
                    kol_id,
                    actor["staff_id"],
                    actor["staff_id"],
                    f"{self.marker}-sku-{idx}",
                    "Smoke Lens",
                    "instagram",
                    "agreed",
                    "active",
                    self.now,
                    self.now,
                    _json({"marker": self.marker, "idx": idx}),
                    self.now,
                    self.now,
                ),
            )
            project_id = int(c.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (project_uid,)).fetchone()["id"])
            c.execute(
                "INSERT INTO vkpi_kol_claims (kol_id, staff_id, project_id, status, claimed_at, last_effective_touch_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (kol_id, actor["staff_id"], project_id, "active", self.now, self.now, _json({"marker": self.marker, "idx": idx}), self.now, self.now),
            )
            actor.update({"idx": idx, "kol_id": kol_id, "project_id": project_id})
            self.actors.append(actor)
        c.commit()

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        user_ids = [int(r["id"]) for r in c.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        kol_ids = [int(r["id"]) for r in c.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        project_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        terms_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_project_terms WHERE note LIKE ? OR sample_terms LIKE ? OR deliverables_json LIKE ?", (like, like, like)).fetchall()]
        deliverable_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_project_deliverables WHERE evidence_url LIKE ? OR note LIKE ?", (like, like)).fetchall()]
        shipment_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_shipments WHERE tracking_number LIKE ? OR evidence_url LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchall()]
        sample_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_sample_assets WHERE serial_number LIKE ? OR note LIKE ? OR metadata_json LIKE ? OR product_sku LIKE ?", (like, like, like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_project_deliverables", "id", deliverable_ids)
        delete_in("vkpi_project_deliverables", "project_id", project_ids)
        delete_in("vkpi_project_terms", "id", terms_ids)
        delete_in("vkpi_project_terms", "project_id", project_ids)
        delete_in("vkpi_shipments", "id", shipment_ids)
        delete_in("vkpi_shipments", "project_id", project_ids)
        delete_in("vkpi_sample_assets", "id", sample_ids)
        delete_in("vkpi_sample_assets", "project_id", project_ids)
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        c.execute(
            "DELETE FROM vkpi_alerts WHERE alert_key LIKE ? OR title LIKE ? OR body LIKE ? OR metadata_json LIKE ?",
            (like, like, like, like),
        )
        if project_ids:
            ph = ",".join("?" for _ in project_ids)
            c.execute(f"DELETE FROM vkpi_alerts WHERE target_type='project' AND target_id IN ({ph})", project_ids)
        if self.actors:
            staff_ids = [int(actor["staff_id"]) for actor in self.actors if actor.get("staff_id")]
            if staff_ids:
                ph = ",".join("?" for _ in staff_ids)
                c.execute(f"DELETE FROM vkpi_alerts WHERE staff_id IN ({ph})", staff_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        c.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ?", (like, like))
        if project_ids:
            ph = ",".join("?" for _ in project_ids)
            c.execute(f"DELETE FROM vkpi_business_audit_logs WHERE target_type='project' AND target_id IN ({ph})", [str(i) for i in project_ids])
        if deliverable_ids:
            ph = ",".join("?" for _ in deliverable_ids)
            c.execute(f"DELETE FROM vkpi_business_audit_logs WHERE target_type='deliverable' AND target_id IN ({ph})", [str(i) for i in deliverable_ids])
        if shipment_ids:
            ph = ",".join("?" for _ in shipment_ids)
            c.execute(f"DELETE FROM vkpi_business_audit_logs WHERE target_type='shipment' AND target_id IN ({ph})", [str(i) for i in shipment_ids])
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        c.commit()
        return {
            "users": int(c.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "kols": int(c.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "terms": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_project_terms WHERE note LIKE ? OR sample_terms LIKE ? OR deliverables_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "deliverables": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_project_deliverables WHERE evidence_url LIKE ? OR note LIKE ?", (like, like)).fetchone()["n"]),
            "shipments": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_shipments WHERE tracking_number LIKE ? OR evidence_url LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "samples": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_sample_assets WHERE serial_number LIKE ? OR note LIKE ? OR metadata_json LIKE ? OR product_sku LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "alerts": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_alerts WHERE alert_key LIKE ? OR title LIKE ? OR body LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "business_audit": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
        }

    def worker_flow(self, actor: dict[str, Any]) -> dict[str, Any]:
        idx = actor["idx"]
        token = actor["token"]
        project_id = int(actor["project_id"])
        terms = self.request_json(
            "POST",
            "/api/marketing/terms",
            {
                "project_id": project_id,
                "cash_fee_usd": 100 + idx,
                "sample_terms": f"{self.marker} sample terms {idx}",
                "usage_rights": "organic repost allowed",
                "due_at": self.now,
                "note": f"{self.marker} terms note {idx}",
                "deliverables": [
                    {"deliverable_type": "video", "quantity": 1, "status": "planned", "due_at": self.now, "note": f"{self.marker} video {idx}"},
                    {"deliverable_type": "story", "quantity": 2, "status": "planned", "due_at": self.now, "note": f"{self.marker} story {idx}"},
                ],
            },
            token=token,
        )
        terms_id = int((terms.get("terms") or {}).get("id") or 0)
        deliverables = terms.get("deliverables") or []
        if not terms_id or len(deliverables) != 2:
            raise AssertionError(f"terms/deliverables mismatch: {terms}")
        deliverable_id = int(deliverables[0]["id"])
        updated_deliverable = self.request_json(
            "PATCH",
            f"/api/marketing/deliverables/{deliverable_id}",
            {"status": "delivered", "delivered_at": self.now, "evidence_url": f"https://evidence.example/{self.marker}/deliverable-{idx}", "note": f"{self.marker} delivered {idx}"},
            token=token,
        )
        if updated_deliverable.get("status") != "delivered":
            raise AssertionError(f"deliverable update failed: {updated_deliverable}")
        shipment = self.request_json(
            "POST",
            "/api/marketing/shipments",
            {
                "project_id": project_id,
                "carrier": "DHL",
                "tracking_number": f"{self.marker}-TRACK-{idx}",
                "shipping_cost_usd": 20 + idx,
                "sample_cost_usd": 499,
                "serial_number": f"{self.marker}-SN-{idx}",
                "return_required": False,
                "evidence_url": f"https://evidence.example/{self.marker}/ship-{idx}",
                "note": f"{self.marker} shipment {idx}",
                "metadata": {"marker": self.marker, "idx": idx},
            },
            token=token,
        )
        shipment_id = int(shipment.get("id") or 0)
        if not shipment_id:
            raise AssertionError(f"shipment id missing: {shipment}")
        if shipment.get("sample_cost_cents") is not None:
            raise AssertionError("operator shipment response exposed sample_cost_cents")
        received = self.request_json(
            "POST",
            f"/api/marketing/shipments/{shipment_id}/receive",
            {"delivered_at": self.now, "note": f"{self.marker} received {idx}"},
            token=token,
        )
        if received.get("status") != "delivered":
            raise AssertionError(f"receive failed: {received}")
        terms_list = self.request_json("GET", f"/api/marketing/terms?project_id={project_id}&limit=10", token=token)
        shipments = self.request_json("GET", f"/api/marketing/shipments?project_id={project_id}&limit=10", token=token)
        samples = self.request_json("GET", f"/api/marketing/samples?project_id={project_id}&limit=10", token=token)
        if int(terms_list.get("count") or 0) != 1 or int(shipments.get("count") or 0) != 1 or int(samples.get("count") or 0) != 1:
            raise AssertionError({"terms": terms_list, "shipments": shipments, "samples": samples})
        sample_rows = samples.get("samples") or []
        if sample_rows and sample_rows[0].get("sample_cost_cents") is not None:
            raise AssertionError("operator samples list exposed sample_cost_cents")
        return {"idx": idx, "terms_id": terms_id, "deliverable_id": deliverable_id, "shipment_id": shipment_id}

    def run(self) -> dict[str, Any]:
        started = time.perf_counter()
        ensure_vkpi_schema()
        ensure_vkpi_audit_schema()
        self.cleanup()
        self.seed()
        first_project = int(self.actors[0]["project_id"])
        self.request_json("GET", f"/api/marketing/terms?project_id={first_project}&limit=10", token=self.outsider["token"], expected_status=403)
        self.request_json("GET", f"/api/marketing/shipments?project_id={first_project}&limit=10", token=self.outsider["token"], expected_status=403)
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(self.worker_flow, actor) for actor in self.actors]
            for future in as_completed(futures, timeout=TOTAL_TIMEOUT_SEC):
                results.append(future.result())
        if len(results) != WORKERS:
            raise AssertionError(f"not all workers completed: {len(results)} / {WORKERS}")
        project_ids = [str(actor["project_id"]) for actor in self.actors]
        deliverable_ids = [str(item["deliverable_id"]) for item in results]
        shipment_ids = [str(item["shipment_id"]) for item in results]

        def audit_count_for(action: str, target_type: str, ids: list[str]) -> int:
            ph = ",".join("?" for _ in ids)
            return int(
                self.conn.execute(
                    f"SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type=? AND target_type=? AND target_id IN ({ph})",
                    [action, target_type, *ids],
                ).fetchone()["n"]
            )

        audit_breakdown = {
            "terms_upsert": audit_count_for("terms_upsert", "project", project_ids),
            "deliverable_update": audit_count_for("deliverable_update", "deliverable", deliverable_ids),
            "shipment_add": audit_count_for("shipment_add", "shipment", shipment_ids),
            "shipment_update": audit_count_for("shipment_update", "shipment", shipment_ids),
        }
        if any(value < WORKERS for value in audit_breakdown.values()):
            raise AssertionError(f"missing business audit rows: {audit_breakdown}")
        audit_count = sum(audit_breakdown.values())
        residue = self.cleanup()
        if any(residue.values()):
            raise AssertionError(f"smoke residue not cleaned: {residue}")
        elapsed_sec = round(time.perf_counter() - started, 3)
        return {
            "ok": True,
            "marker": self.marker,
            "workers": WORKERS,
            "completed": len(results),
            "elapsed_sec": elapsed_sec,
            "throughput_workers_per_sec": round(len(results) / elapsed_sec, 3) if elapsed_sec else len(results),
            "request_timeout_sec": REQUEST_TIMEOUT_SEC,
            "total_timeout_sec": TOTAL_TIMEOUT_SEC,
            "audit_count": audit_count,
            "audit_breakdown": audit_breakdown,
            "residue": residue,
        }


if __name__ == "__main__":
    smoke = Smoke()
    try:
        print(json.dumps(smoke.run(), ensure_ascii=False, indent=2))
    except Exception:
        residue = smoke.cleanup()
        print(json.dumps({"ok": False, "marker": smoke.marker, "cleanup_after_failure": residue}, ensure_ascii=False, indent=2))
        raise
