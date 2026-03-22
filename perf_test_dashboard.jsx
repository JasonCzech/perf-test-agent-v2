import { useState, useEffect, useRef, useCallback } from "react";

const PHASES = [
  { id: "story_analysis", label: "Story Analysis", icon: "📋", num: 1 },
  { id: "test_planning", label: "Test Planning", icon: "📝", num: 2 },
  { id: "env_triage", label: "Env Triage", icon: "🔧", num: 3 },
  { id: "script_data", label: "Scripts & Data", icon: "⚙️", num: 4 },
  { id: "execution", label: "Execution", icon: "🚀", num: 5 },
  { id: "reporting", label: "Reporting", icon: "📊", num: 6 },
  { id: "postmortem", label: "Postmortem", icon: "🔄", num: 7 },
];

const STATUS_COLORS = {
  pending: "#64748b",
  running: "#f59e0b",
  awaiting_approval: "#8b5cf6",
  approved: "#22c55e",
  completed: "#22c55e",
  rejected: "#ef4444",
  failed: "#ef4444",
  skipped: "#94a3b8",
};

// ── Mock data for demonstration ──────────────────────────────────────
const MOCK_STATE = {
  run_id: "run-20260322-143022-a1b2c3",
  current_phase: "env_triage",
  created_at: new Date().toISOString(),
  story_keys: ["TELECOM-4521", "TELECOM-4522"],
  phase_results: {
    story_analysis: { status: "completed", summary: "Analyzed 2 stories, extracted 6 test cases.\nSLA targets: 12\nHigh risk: 2\nConfidence: 87%", duration_seconds: 45 },
    test_planning: { status: "completed", summary: "Test Plan: Q1 2026 Billing Regression\nScenarios: load(30min), stress(45min), endurance(120min)\nSystems: CSI, TLG, BSSe\nData prep: 4 steps\nRisks: 2 high", duration_seconds: 62 },
    env_triage: { status: "awaiting_approval", summary: "Total checks: 24\nPassed: 22 | Failed: 2 | Errors: 0\nGolden config: NO\nMismatches: billing-api.BSSE_ENDPOINT pointing to QC3; gddn.SOLACE_VPN = qc3-vpn", duration_seconds: 18 },
  },
};

function StatusBadge({ status }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 10px", borderRadius: 999, fontSize: 11, fontWeight: 600,
      letterSpacing: "0.03em", textTransform: "uppercase",
      background: `${STATUS_COLORS[status] || "#64748b"}18`,
      color: STATUS_COLORS[status] || "#64748b",
      border: `1px solid ${STATUS_COLORS[status] || "#64748b"}30`,
    }}>
      {status === "running" && <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: STATUS_COLORS.running, animation: "pulse 1.5s infinite" }} />}
      {status?.replace("_", " ")}
    </span>
  );
}

function PhaseCard({ phase, result, isActive, onClick }) {
  const status = result?.status || "pending";
  const borderColor = isActive ? "#8b5cf6" : status === "completed" ? "#22c55e40" : "transparent";

  return (
    <div onClick={onClick} style={{
      background: isActive ? "#1e1b4b08" : "white",
      border: `2px solid ${borderColor}`,
      borderRadius: 12, padding: "14px 16px", cursor: "pointer",
      transition: "all 0.2s ease",
      boxShadow: isActive ? "0 4px 20px #8b5cf620" : "0 1px 3px #0001",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 18 }}>{phase.icon}</span>
          <div>
            <span style={{ fontSize: 10, color: "#94a3b8", fontWeight: 700, letterSpacing: "0.08em" }}>PHASE {phase.num}</span>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#1e293b" }}>{phase.label}</div>
          </div>
        </div>
        <StatusBadge status={status} />
      </div>
      {result?.duration_seconds && (
        <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 4 }}>
          Duration: {result.duration_seconds.toFixed(1)}s
        </div>
      )}
    </div>
  );
}

function HITLPanel({ phase, result, onApprove, onReject }) {
  const [notes, setNotes] = useState("");

  if (!result || result.status !== "awaiting_approval") return null;

  return (
    <div style={{
      background: "linear-gradient(135deg, #4c1d95 0%, #6d28d9 100%)",
      borderRadius: 16, padding: 24, color: "white",
      boxShadow: "0 8px 32px #4c1d9540",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <div style={{ width: 40, height: 40, borderRadius: "50%", background: "#ffffff20", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20 }}>⏸</div>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700 }}>Approval Required</div>
          <div style={{ fontSize: 12, opacity: 0.8 }}>Phase {phase.num}: {phase.label}</div>
        </div>
      </div>

      <div style={{ background: "#ffffff15", borderRadius: 10, padding: 14, marginBottom: 16, fontFamily: "monospace", fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
        {result.summary}
      </div>

      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Approval notes (optional)..."
        style={{
          width: "100%", boxSizing: "border-box", minHeight: 60, padding: 12, borderRadius: 8,
          border: "1px solid #ffffff30", background: "#ffffff10", color: "white",
          fontFamily: "inherit", fontSize: 13, resize: "vertical", marginBottom: 14,
          outline: "none",
        }}
      />

      <div style={{ display: "flex", gap: 10 }}>
        <button
          onClick={() => onApprove(notes)}
          style={{
            flex: 1, padding: "10px 20px", borderRadius: 8, border: "none",
            background: "#22c55e", color: "white", fontWeight: 700, fontSize: 14,
            cursor: "pointer", transition: "transform 0.1s",
          }}
          onMouseDown={(e) => e.target.style.transform = "scale(0.97)"}
          onMouseUp={(e) => e.target.style.transform = "scale(1)"}
        >
          ✓ Approve & Continue
        </button>
        <button
          onClick={() => onReject(notes)}
          style={{
            flex: 1, padding: "10px 20px", borderRadius: 8,
            border: "2px solid #ffffff40", background: "transparent",
            color: "white", fontWeight: 700, fontSize: 14, cursor: "pointer",
          }}
        >
          ✗ Reject
        </button>
      </div>
    </div>
  );
}

function LogPanel({ logs }) {
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs]);

  return (
    <div style={{
      background: "#0f172a", borderRadius: 12, padding: 16,
      fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontSize: 11,
      lineHeight: 1.7, color: "#cbd5e1", maxHeight: 300, overflowY: "auto",
    }}>
      <div style={{ color: "#64748b", marginBottom: 8, fontSize: 10, fontWeight: 700, letterSpacing: "0.1em" }}>
        PIPELINE LOG
      </div>
      {logs.map((log, i) => (
        <div key={i} style={{ color: log.color || "#cbd5e1" }}>
          <span style={{ color: "#475569" }}>[{log.time}]</span>{" "}
          <span style={{ color: log.levelColor || "#64748b" }}>{log.level}</span>{" "}
          {log.message}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}

export default function PerfTestDashboard() {
  const [state, setState] = useState(MOCK_STATE);
  const [selectedPhase, setSelectedPhase] = useState("env_triage");
  const [ws, setWs] = useState(null);
  const [logs, setLogs] = useState([
    { time: "14:30:22", level: "INFO", message: "Pipeline started: run-20260322-143022-a1b2c3", color: "#22c55e", levelColor: "#22c55e" },
    { time: "14:30:24", level: "INFO", message: "Phase 1: Story Analysis — fetching TELECOM-4521, TELECOM-4522", levelColor: "#3b82f6" },
    { time: "14:31:07", level: "INFO", message: "Phase 1: Extracted 6 test cases (confidence: 87%)", levelColor: "#22c55e" },
    { time: "14:31:08", level: "INFO", message: "Phase 1: ✓ Approved by jason.m", levelColor: "#22c55e" },
    { time: "14:31:10", level: "INFO", message: "Phase 2: Test Planning — building workload model", levelColor: "#3b82f6" },
    { time: "14:32:12", level: "INFO", message: "Phase 2: Generated 3 scenarios (load, stress, endurance)", levelColor: "#22c55e" },
    { time: "14:32:13", level: "INFO", message: "Phase 2: ✓ Approved by jason.m", levelColor: "#22c55e" },
    { time: "14:32:15", level: "INFO", message: "Phase 3: Env Triage — validating 24 config fields", levelColor: "#3b82f6" },
    { time: "14:32:33", level: "WARN", message: "Phase 3: billing-api.BSSE_ENDPOINT → pointing to QC3!", color: "#f59e0b", levelColor: "#f59e0b" },
    { time: "14:32:33", level: "WARN", message: "Phase 3: gddn.SOLACE_VPN → qc3-vpn (expected: perf-vpn)", color: "#f59e0b", levelColor: "#f59e0b" },
    { time: "14:32:34", level: "INFO", message: "Phase 3: ⏸ Awaiting approval (2 mismatches found)", levelColor: "#8b5cf6" },
  ]);

  const handleApprove = useCallback((notes) => {
    const updated = { ...state };
    updated.phase_results.env_triage.status = "completed";
    updated.current_phase = "script_data";
    updated.phase_results.script_data = { status: "running", summary: "", duration_seconds: 0 };
    setState(updated);
    setSelectedPhase("script_data");
    setLogs(prev => [
      ...prev,
      { time: new Date().toLocaleTimeString("en-US", { hour12: false }), level: "INFO", message: `Phase 3: ✓ Approved${notes ? ` — "${notes}"` : ""}`, levelColor: "#22c55e" },
      { time: new Date().toLocaleTimeString("en-US", { hour12: false }), level: "INFO", message: "Phase 4: Script & Data Creation — generating VuGen + JMeter scripts", levelColor: "#3b82f6" },
    ]);
  }, [state]);

  const handleReject = useCallback((notes) => {
    const updated = { ...state };
    updated.phase_results.env_triage.status = "rejected";
    setState(updated);
    setLogs(prev => [
      ...prev,
      { time: new Date().toLocaleTimeString("en-US", { hour12: false }), level: "WARN", message: `Phase 3: ✗ Rejected — "${notes || "No notes"}"`, color: "#ef4444", levelColor: "#ef4444" },
    ]);
  }, [state]);

  const activePhaseObj = PHASES.find(p => p.id === selectedPhase);
  const activeResult = state.phase_results[selectedPhase];

  return (
    <div style={{ minHeight: "100vh", background: "#f8fafc", fontFamily: "'DM Sans', 'Segoe UI', sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700&family=JetBrains+Mono:wght@400;600&display=swap');
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        * { margin: 0; padding: 0; }
      `}</style>

      {/* Header */}
      <header style={{
        background: "linear-gradient(135deg, #1e3a5f 0%, #0a2540 100%)",
        padding: "18px 32px", display: "flex", alignItems: "center", justifyContent: "space-between",
        borderBottom: "3px solid #009fdb",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 8,
            background: "linear-gradient(135deg, #009fdb, #0568ae)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 16, fontWeight: 800, color: "white",
          }}>PT</div>
          <div>
            <div style={{ color: "white", fontSize: 16, fontWeight: 700, letterSpacing: "-0.01em" }}>
              PERF-TEST-AGENT
            </div>
            <div style={{ color: "#009fdb", fontSize: 11, fontWeight: 600, letterSpacing: "0.05em" }}>
              CTx CQE Performance Engineering
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ color: "#94a3b8", fontSize: 12 }}>
            Run: <span style={{ color: "#009fdb", fontFamily: "monospace" }}>{state.run_id?.slice(-12)}</span>
          </div>
          <div style={{
            width: 8, height: 8, borderRadius: "50%",
            background: state.phase_results[state.current_phase]?.status === "awaiting_approval" ? "#8b5cf6" : "#22c55e",
            animation: "pulse 1.5s infinite",
          }} />
        </div>
      </header>

      {/* Pipeline Progress Bar */}
      <div style={{ padding: "16px 32px", background: "white", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {PHASES.map((phase, i) => {
            const result = state.phase_results[phase.id];
            const status = result?.status || "pending";
            const color = STATUS_COLORS[status] || "#e2e8f0";
            return (
              <div key={phase.id} style={{ display: "flex", alignItems: "center", flex: 1 }}>
                <div
                  onClick={() => setSelectedPhase(phase.id)}
                  style={{
                    width: 28, height: 28, borderRadius: "50%",
                    background: status === "pending" ? "#f1f5f9" : color,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 12, fontWeight: 700,
                    color: status === "pending" ? "#94a3b8" : "white",
                    cursor: "pointer", transition: "transform 0.15s",
                    boxShadow: selectedPhase === phase.id ? `0 0 0 3px ${color}40` : "none",
                  }}
                >
                  {status === "completed" ? "✓" : phase.num}
                </div>
                {i < PHASES.length - 1 && (
                  <div style={{
                    flex: 1, height: 2, marginLeft: 4, marginRight: 4,
                    background: status === "completed" ? "#22c55e" : "#e2e8f0",
                  }} />
                )}
              </div>
            );
          })}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
          {PHASES.map(p => (
            <div key={p.id} style={{ fontSize: 9, color: "#94a3b8", textAlign: "center", flex: 1, fontWeight: 600 }}>
              {p.label}
            </div>
          ))}
        </div>
      </div>

      {/* Main Content */}
      <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: 24, padding: 24, maxWidth: 1400, margin: "0 auto" }}>
        {/* Phase List */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#64748b", letterSpacing: "0.08em", marginBottom: 4 }}>
            PIPELINE PHASES
          </div>
          {PHASES.map(phase => (
            <PhaseCard
              key={phase.id}
              phase={phase}
              result={state.phase_results[phase.id]}
              isActive={selectedPhase === phase.id}
              onClick={() => setSelectedPhase(phase.id)}
            />
          ))}
        </div>

        {/* Detail Panel */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* HITL Approval Panel */}
          {activePhaseObj && (
            <HITLPanel
              phase={activePhaseObj}
              result={activeResult}
              onApprove={handleApprove}
              onReject={handleReject}
            />
          )}

          {/* Phase Detail */}
          {activeResult && activeResult.status !== "awaiting_approval" && (
            <div style={{ background: "white", borderRadius: 12, padding: 20, boxShadow: "0 1px 3px #0001" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
                <span style={{ fontSize: 22 }}>{activePhaseObj?.icon}</span>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 700, color: "#1e293b" }}>
                    Phase {activePhaseObj?.num}: {activePhaseObj?.label}
                  </div>
                  <StatusBadge status={activeResult.status} />
                </div>
              </div>
              {activeResult.summary && (
                <pre style={{
                  background: "#f8fafc", borderRadius: 8, padding: 14,
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
                  lineHeight: 1.7, color: "#334155", whiteSpace: "pre-wrap",
                  border: "1px solid #e2e8f0",
                }}>
                  {activeResult.summary}
                </pre>
              )}
            </div>
          )}

          {/* Jira Stories */}
          <div style={{ background: "white", borderRadius: 12, padding: 20, boxShadow: "0 1px 3px #0001" }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: "#64748b", letterSpacing: "0.08em", marginBottom: 12 }}>
              SOURCE STORIES
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              {state.story_keys?.map(key => (
                <span key={key} style={{
                  padding: "6px 12px", borderRadius: 6,
                  background: "#eff6ff", color: "#1d4ed8",
                  fontSize: 12, fontWeight: 600, fontFamily: "monospace",
                }}>
                  {key}
                </span>
              ))}
            </div>
          </div>

          {/* Log Panel */}
          <LogPanel logs={logs} />
        </div>
      </div>
    </div>
  );
}
