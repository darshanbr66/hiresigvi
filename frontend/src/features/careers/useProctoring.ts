import { useEffect, useRef } from "react";
import type { MonitoringEventCreate, MonitoringEventType } from "../../types/assessment";
import { sendMonitoringEvents } from "./api";

const FLUSH_INTERVAL_MS = 4000;

/**
 * Wires the browser-observable monitoring events disclosed on the
 * "Before You Begin" screen (MonitoringConsentScreen.tsx) — active only
 * while the candidate's attempt is STARTED. Events are queued and flushed
 * in small batches rather than one request per event; a failed/dropped
 * flush never blocks or fails the candidate's attempt (ZPHIREX platform
 * overhaul § 6). Only listens for what a standard browser can reliably
 * report — see docs/assessment.md for documented limitations (e.g.
 * `devicechange` only fires on add/remove, not a live permission
 * revocation, and isn't supported in every browser).
 */
export function useProctoring(token: string, active: boolean) {
  const queueRef = useRef<MonitoringEventCreate[]>([]);

  useEffect(() => {
    if (!active) return;

    function push(eventType: MonitoringEventType) {
      queueRef.current.push({ event_type: eventType, occurred_at: new Date().toISOString() });
    }

    function handleVisibility() {
      if (document.hidden) push("TAB_SWITCH");
    }
    function handleBlur() {
      push("WINDOW_BLUR");
    }
    function handleFocus() {
      push("WINDOW_FOCUS");
    }
    function handleFullscreenChange() {
      if (!document.fullscreenElement) push("FULLSCREEN_EXIT");
    }
    function handleOffline() {
      push("CONNECTION_INTERRUPTED");
    }
    function handleOnline() {
      push("CONNECTION_RESTORED");
    }

    let lastCameraCount = -1;
    let lastMicrophoneCount = -1;

    async function checkDevices() {
      if (!navigator.mediaDevices?.enumerateDevices) return;
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const cameras = devices.filter((d) => d.kind === "videoinput").length;
        const microphones = devices.filter((d) => d.kind === "audioinput").length;
        if (lastCameraCount !== -1 && cameras !== lastCameraCount) push("CAMERA_DEVICE_CHANGED");
        if (lastMicrophoneCount !== -1 && microphones !== lastMicrophoneCount) {
          push("MICROPHONE_DEVICE_CHANGED");
        }
        if (cameras === 0) push("CAMERA_UNAVAILABLE");
        if (microphones === 0) push("MICROPHONE_UNAVAILABLE");
        lastCameraCount = cameras;
        lastMicrophoneCount = microphones;
      } catch {
        // enumerateDevices can be denied/unsupported in some contexts —
        // nothing reliable to record.
      }
    }

    void checkDevices();

    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("blur", handleBlur);
    window.addEventListener("focus", handleFocus);
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    window.addEventListener("offline", handleOffline);
    window.addEventListener("online", handleOnline);
    navigator.mediaDevices?.addEventListener?.("devicechange", checkDevices);

    function flush() {
      if (queueRef.current.length === 0) return;
      const batch = queueRef.current.splice(0, queueRef.current.length);
      sendMonitoringEvents(token, batch).catch(() => {
        // Best-effort: a dropped monitoring event must never block or fail
        // the candidate's assessment attempt.
      });
    }

    const interval = setInterval(flush, FLUSH_INTERVAL_MS);

    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("blur", handleBlur);
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("online", handleOnline);
      navigator.mediaDevices?.removeEventListener?.("devicechange", checkDevices);
      clearInterval(interval);
      flush();
    };
  }, [active, token]);
}
