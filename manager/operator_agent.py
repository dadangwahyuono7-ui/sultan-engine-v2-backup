from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sultan_engine_v2.manager.ai_manager import ManagerReport, SultanAIManager
from sultan_engine_v2.manager.openrouter_client import OpenRouterClient
from sultan_engine_v2.runtime.file_utils import atomic_write_json


DEFAULT_DOCTRINE_SUMMARY = """
- CMP selalu directional, tidak ada netral setelah bootstrap.
- Urutan analisa adalah CMP -> VR -> CF.
- VR adalah breakout lawan pertama terhadap parent/master terdekat.
- CF hanya valid setelah VR dan harus kembali searah parent/master yang masih bertahan.
- Jika VR berhasil flip master, sequence lama mati dan battle naik ke timeframe atas.
- Fokus trading mengikuti highest relevant CMP yang benar-benar flip, bukan breakout lawan kecil yang terisolasi.
- Target utama mengikuti SNR timeframe di atas timeframe CF, bukan TP acak.
- Agent tidak boleh mengarang signal, entry, exit, atau override risk di luar data yang tersedia.
""".strip()


@dataclass(slots=True)
class OperatorAgentAnswer:
    question: str
    response_text: str
    interpreter: str
    created_at: str
    snapshot_timestamp: str | None = None


class JsonOperatorConversationStore:
    def __init__(self, path: str | Path | None = None, max_turns_per_sender: int = 6) -> None:
        default_path = Path(__file__).resolve().parent.parent / "runtime" / "state" / "operator_agent_threads.json"
        self.path = Path(path) if path else default_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_turns_per_sender = max_turns_per_sender

    def get_history(self, sender: str, limit: int | None = None) -> list[dict[str, Any]]:
        payload = self._load()
        turns = payload.get(sender, [])
        if limit is None:
            return turns
        return turns[-limit:]

    def append_turn(self, sender: str, question: str, response_text: str, interpreter: str) -> None:
        payload = self._load()
        sender_key = sender or "operator"
        turns = payload.get(sender_key, [])
        turns.append(
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "question": question,
                "response_text": response_text,
                "interpreter": interpreter,
            }
        )
        payload[sender_key] = turns[-self.max_turns_per_sender :]
        self._save(payload)

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, dict):
            return data
        return {}

    def _save(self, payload: dict[str, list[dict[str, Any]]]) -> None:
        atomic_write_json(self.path, payload)


class SultanOperatorAgent:
    def __init__(
        self,
        snapshot_path: str | Path | None = None,
        doctrine_path: str | Path | None = None,
        openrouter: OpenRouterClient | None = None,
        manager: SultanAIManager | None = None,
        thread_store: JsonOperatorConversationStore | None = None,
    ) -> None:
        runtime_dir = Path(__file__).resolve().parent.parent / "runtime"
        self.snapshot_path = Path(snapshot_path) if snapshot_path else runtime_dir / "state" / "latest_snapshot.json"
        self.doctrine_path = Path(doctrine_path) if doctrine_path else Path(__file__).resolve().parent.parent.parent / "daily_deploy_rules.md"
        self.openrouter = openrouter or OpenRouterClient()
        self.manager = manager or SultanAIManager()
        self.thread_store = thread_store or JsonOperatorConversationStore(runtime_dir / "state" / "operator_agent_threads.json")
        self.doctrine_text = self._load_doctrine_text()

    def answer(self, question: str, sender: str = "operator", snapshot: dict | None = None) -> OperatorAgentAnswer:
        active_snapshot = snapshot if snapshot is not None else self._load_snapshot()
        report = self.manager.audit_snapshot(active_snapshot) if active_snapshot else None
        response_text: str | None = None
        interpreter = "RULE_AGENT"

        if self.openrouter.is_enabled:
            response_text = self._answer_with_model(question, sender=sender, snapshot=active_snapshot, report=report)
            if response_text:
                interpreter = "OPENROUTER_AGENT"

        if not response_text:
            response_text = self._fallback_answer(question, active_snapshot, report)

        self.thread_store.append_turn(sender, question, response_text, interpreter)
        return OperatorAgentAnswer(
            question=question,
            response_text=response_text,
            interpreter=interpreter,
            created_at=datetime.now(timezone.utc).isoformat(),
            snapshot_timestamp=(active_snapshot or {}).get("timestamp"),
        )

    def _answer_with_model(self, question: str, sender: str, snapshot: dict | None, report: ManagerReport | None) -> str | None:
        history = self.thread_store.get_history(sender, limit=4)
        context = self._build_context_packet(snapshot, report)
        fact_sheet = self._build_fact_sheet(context)
        user_prompt = "\n\n".join(
            [
                f"Operator question:\n{question}",
                f"Daily Deploy SOP / doctrine:\n{self._doctrine_excerpt()}",
                f"Recent conversation:\n{self._render_history(history)}",
                f"Grounded fact sheet:\n{fact_sheet}",
                (
                    "Answer with grounded operational analysis only. Separate: "
                    "1) kesimpulan utama, 2) struktur SOP CMP/VR/CF, 3) status engine/execution, 4) next watch. "
                    "Do not add new VR, CF, controller flips, or ready intents unless they explicitly appear in the grounded fact sheet. "
                    "If context is missing, say so clearly."
                ),
            ]
        )
        response_text = self.openrouter.complete_text(self._system_prompt(), user_prompt)
        if not response_text:
            return None
        if not self._is_grounded_answer(response_text, context):
            return None
        return response_text

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are Sultan Operator Agent for a live MT5 CFD engine. "
            "Speak in Bahasa Indonesia, casual bro style, but be precise and disciplined. "
            "You must follow the supplied Daily Deploy doctrine: CMP is always directional, VR is the first counter breakout, CF only exists after VR, and control follows the highest relevant CMP master. "
            "You are allowed to analyze engine state, market structure, readiness, guard blocks, and what the engine is waiting for next. "
            "You are not allowed to invent market data, trade signals, entries, exits, or risk overrides that are not grounded in the provided context. "
            "If the user asks beyond available context, say data is insufficient."
        )

    def _fallback_answer(self, question: str, snapshot: dict | None, report: ManagerReport | None) -> str:
        if snapshot is None:
            return (
                "Snapshot runtime belum ada bro, jadi gue belum bisa analisa engine atau market live. "
                "Jalankan runtime MT5 dulu, baru gue bisa baca CMP, VR, CF, dan guard state sesuai SOP."
            )

        context = self._build_context_packet(snapshot, report)
        cmp_chain = " | ".join(context["cmp_chain"]) if context["cmp_chain"] else "-"
        control_summary = " | ".join(context["parent_control"]) if context["parent_control"] else "-"
        block_summary = ", ".join(context["guard_block_reasons"]) if context["guard_block_reasons"] else "none"
        finding_summary = "; ".join(context["audit_findings"][:3]) if context["audit_findings"] else "no audit finding"
        ready_count = len(context["ready_intents"])
        vr_text = ", ".join(context["active_vr_parents"]) if context["active_vr_parents"] else "belum ada VR aktif"
        cf_text = ", ".join(context["confirmed_cf_parents"]) if context["confirmed_cf_parents"] else "belum ada CF confirmed"
        setup_focus_text = self._render_setup_focus(context.get("setup_focus", {}))

        return "\n".join(
            [
                f"Kesimpulan bro: engine lagi baca {context['symbol']} dengan CMP chain {cmp_chain}.",
                f"Struktur SOP: {vr_text}; {cf_text}. Focus aktif {setup_focus_text}. Parent control sekarang {control_summary}.",
                f"Status engine: {ready_count} candidate ready, guard block terakhir/recent reason = {block_summary}.",
                f"Audit ringkas: {finding_summary}.",
                "Next watch: tunggu VR valid lawan master atau CF recovery yang benar-benar confirm sebelum anggap ada setup baru.",
                f"Pertanyaan lo tadi: {question}",
            ]
        )

    def _build_context_packet(self, snapshot: dict | None, report: ManagerReport | None) -> dict[str, Any]:
        if snapshot is None:
            return {
                "snapshot_timestamp": None,
                "symbol": "UNKNOWN",
                "cmp_chain": [],
                "parent_control": [],
                "active_vr_parents": [],
                "ready_intents": [],
                "audit_summary": report.summary if report else "Snapshot missing",
                "audit_findings": [],
                "guard_block_reasons": [],
                "recent_logs": [],
            }

        timeframes = snapshot.get("timeframes", {})
        sequences = snapshot.get("sequences", {})
        setup_focus = snapshot.get("setup_focus", {})
        decisions = snapshot.get("execution", {}).get("recent_decisions", [])
        blocked_reasons = sorted({decision.get("reason", "UNKNOWN") for decision in decisions if not decision.get("allowed", True)})
        position_structure = snapshot.get("lifecycle", {}).get("position_structure", [])

        cmp_chain = []
        for timeframe in ("D1", "H4", "H1", "M30", "M15", "M5"):
            if timeframe in timeframes:
                cmp_chain.append(f"{timeframe}:{timeframes[timeframe].get('cmp') or '-'}")

        parent_control = []
        active_vr_parents = []
        confirmed_cf_parents = []
        for parent in ("D1", "H4", "H1", "M30", "M15"):
            sequence = sequences.get(parent)
            if not sequence:
                continue
            state = sequence.get("control_state") or "-"
            parent_control.append(f"{parent}:{state}:{sequence.get('active_controller') or '-'}")
            if sequence.get("vr_active"):
                active_vr_parents.append(parent)
            if int(sequence.get("cf_count") or 0) > 0 or state == "RECOVERED_BY_CF":
                confirmed_cf_parents.append(parent)

        ready_intents = [asdict(intent) for intent in report.ready_intents] if report else []
        audit_findings = [f"[{item.severity}] {item.code}: {item.message}" for item in (report.findings if report else [])]

        return {
            "snapshot_timestamp": snapshot.get("timestamp"),
            "symbol": snapshot.get("symbol", {}).get("actual") or snapshot.get("symbol", {}).get("requested") or "UNKNOWN",
            "venue": snapshot.get("venue") or "MT5",
            "market": snapshot.get("market", {}),
            "cmp_chain": cmp_chain,
            "parent_control": parent_control,
            "active_vr_parents": active_vr_parents,
            "confirmed_cf_parents": confirmed_cf_parents,
            "ready_intents": ready_intents,
            "audit_summary": report.summary if report else "No audit available",
            "audit_findings": audit_findings,
            "guard_block_reasons": blocked_reasons,
            "recent_logs": snapshot.get("logs", [])[-8:],
            "operator": snapshot.get("operator", {}),
            "setup_focus": setup_focus,
            "position_structure": position_structure,
        }

    @staticmethod
    def _build_fact_sheet(context: dict[str, Any]) -> str:
        lines = [
            f"Symbol: {context.get('symbol', 'UNKNOWN')}",
            f"Snapshot timestamp: {context.get('snapshot_timestamp') or 'missing'}",
            f"CMP chain: {' | '.join(context.get('cmp_chain', [])) or '-'}",
            f"Parent control: {' | '.join(context.get('parent_control', [])) or '-'}",
            f"Active VR parents: {', '.join(context.get('active_vr_parents', [])) or 'none'}",
            f"Confirmed CF parents: {', '.join(context.get('confirmed_cf_parents', [])) or 'none'}",
            f"Setup focus: {SultanOperatorAgent._render_setup_focus(context.get('setup_focus', {}))}",
            f"Open position structure: {SultanOperatorAgent._render_position_structure(context.get('position_structure', []))}",
            f"Ready intents: {len(context.get('ready_intents', []))}",
            f"Guard block reasons: {', '.join(context.get('guard_block_reasons', [])) or 'none'}",
            f"Audit summary: {context.get('audit_summary', 'No audit available')}",
        ]
        findings = context.get("audit_findings", [])[:5]
        if findings:
            lines.append("Audit findings:")
            lines.extend(f"- {item}" for item in findings)
        logs = context.get("recent_logs", [])[-5:]
        if logs:
            lines.append("Recent logs:")
            lines.extend(f"- {item}" for item in logs)
        return "\n".join(lines)

    @staticmethod
    def _is_grounded_answer(response_text: str, context: dict[str, Any]) -> bool:
        lower_text = response_text.lower()
        if not context.get("active_vr_parents"):
            vr_hallucination_markers = [
                "dalam status vr",
                "adalah vr",
                "vr terhadap",
                "vr aktif pada",
            ]
            if any(marker in lower_text for marker in vr_hallucination_markers) and "belum ada vr aktif" not in lower_text:
                return False

        if not context.get("confirmed_cf_parents"):
            cf_hallucination_markers = [
                "telah menghasilkan cf",
                "sudah ada cf",
                "cf valid sudah muncul",
                "cf confirmed pada",
            ]
            if any(marker in lower_text for marker in cf_hallucination_markers) and "belum ada cf" not in lower_text:
                return False

        return True

    @staticmethod
    def _render_setup_focus(setup_focus: dict[str, Any]) -> str:
        status = str(setup_focus.get("status") or "NO_ACTIVE_VR")
        master = setup_focus.get("master_timeframe") or "-"
        if status == "WAIT_CF":
            return (
                f"WAIT_CF master {master} | vr {setup_focus.get('vr_timeframe') or '-'} | "
                f"cf low {setup_focus.get('cf_low_risk_timeframe') or '-'} | cf high {setup_focus.get('cf_high_risk_timeframe') or '-'}"
            )
        if status == "CF_CONFIRMED":
            return f"CF_CONFIRMED master {master} via {setup_focus.get('confirmed_cf_timeframe') or '-'}"
        return f"NO_ACTIVE_VR | master {master}"

    @staticmethod
    def _render_position_structure(position_structure: list[dict[str, Any]]) -> str:
        if not position_structure:
            return "no managed open position"
        parts: list[str] = []
        for item in position_structure[:3]:
            parts.append(
                f"#{item.get('ticket')} {item.get('status')} {item.get('entry_kind')} {item.get('parent_timeframe')}/{item.get('cf_timeframe')}"
            )
        return " | ".join(parts)

    def _load_doctrine_text(self) -> str:
        if not self.doctrine_path.exists():
            return DEFAULT_DOCTRINE_SUMMARY
        try:
            text = self.doctrine_path.read_text(encoding="utf-8").strip()
        except OSError:
            return DEFAULT_DOCTRINE_SUMMARY
        return text or DEFAULT_DOCTRINE_SUMMARY

    def _doctrine_excerpt(self, max_chars: int = 6000) -> str:
        if len(self.doctrine_text) <= max_chars:
            return self.doctrine_text
        return self.doctrine_text[:max_chars] + "\n...[truncated]"

    def _load_snapshot(self) -> dict | None:
        if not self.snapshot_path.exists():
            return None
        try:
            return json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _render_history(history: list[dict[str, Any]]) -> str:
        if not history:
            return "No previous conversation turns recorded."
        lines: list[str] = []
        for turn in history:
            lines.append(f"Q: {turn.get('question', '-')}")
            lines.append(f"A: {turn.get('response_text', '-')}")
        return "\n".join(lines)
