import { useState } from "react";

/**
 * "Before You Begin" — transparent disclosure of assessment monitoring
 * (ZPHIREX platform overhaul § 5). Only lists events this build actually
 * implements (see useProctoring.ts) — never overclaims a capability the
 * browser can't reliably provide.
 */
export function MonitoringConsentScreen({
  onAccept,
  isStarting,
}: {
  onAccept: () => void;
  isStarting: boolean;
}) {
  const [agreed, setAgreed] = useState(false);

  return (
    <section className="card stack-lg" style={{ gap: "1rem" }}>
      <h2>Before You Begin</h2>
      <p className="muted">
        This assessment uses browser-based monitoring to protect assessment integrity. During the
        assessment, the following may be recorded with a timestamp and reviewed by the recruitment
        team:
      </p>
      <ul className="check-list">
        <li>Camera and microphone permission, and whether they become unavailable</li>
        <li>Camera/microphone device changes (e.g. unplugging a webcam)</li>
        <li>Switching away from this tab or window</li>
        <li>Exiting fullscreen, if fullscreen is in use</li>
        <li>Internet connection interruptions</li>
      </ul>
      <p className="field-hint">
        We do not record or store audio or video — only the events above, with a timestamp. Camera
        and microphone access (if you grant it) is used solely to detect whether they're available;
        no stream is captured.
      </p>
      <label style={{ display: "flex", alignItems: "flex-start", gap: "0.6rem" }}>
        <input
          type="checkbox"
          checked={agreed}
          onChange={(e) => setAgreed(e.target.checked)}
          style={{ marginTop: "0.2rem", width: "auto" }}
        />
        <span>I understand and agree to browser-based assessment monitoring.</span>
      </label>
      <button
        type="button"
        className="btn btn-primary"
        disabled={!agreed || isStarting}
        onClick={onAccept}
      >
        {isStarting ? "Starting…" : "Start Assessment"}
      </button>
    </section>
  );
}
