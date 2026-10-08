import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import { useReducedMotion } from "../../../shared/hooks/useReducedMotion";
import { Icon } from "../../../shared/components/Icon";
import { Spinner } from "../../../shared/components/Spinner";
import type { DepartmentResponse, EmployeeResponse } from "../../../types/teamHierarchy";
import { moveEmployeeId, type DropPosition } from "./employeeOrder";
import "./orgChart.css";

const UNASSIGNED_KEY = "__unassigned__";
// Up to this many active employees the chart opens fully expanded (the
// familiar org-chart look); beyond it, departments start collapsed so a large
// organization doesn't open as a wall of cards.
const AUTO_EXPAND_LIMIT = 40;

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 1.5;
const ZOOM_STEP = 0.1;
/** The smallest zoom the chart picks by itself to show everything at once. */
const AUTO_FIT_MIN_ZOOM = 0.7;
/** How many member avatars a collapsed department previews. */
const AVATAR_PREVIEW = 3;

/** Department accents, cycled in department order — existing theme tokens,
 * each with its own dark-mode value, so both themes stay balanced. */
const ACCENTS = [
  "var(--kind-candidate)",
  "var(--kind-assessment)",
  "var(--kind-job)",
  "var(--kind-match)",
  "var(--kind-ai)",
  "var(--kind-organization)",
];
const UNASSIGNED_ACCENT = "var(--kind-skill)";

interface Branch {
  key: string;
  // null for the Unassigned group, which is ordered like any department.
  departmentId: string | null;
  name: string;
  employees: EmployeeResponse[];
  unassigned: boolean;
  accent: string;
}

interface DragState {
  branchKey: string;
  employeeId: string;
}

interface DropTarget {
  employeeId: string;
  position: DropPosition;
}

function employeeCountLabel(count: number): string {
  return `${count} ${count === 1 ? "employee" : "employees"}`;
}

/** Up to two initials from the words of a name that start with a letter —
 * never digits, so a card never shows a number. */
function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter((word) => /^\p{L}/u.test(word));
  if (words.length === 0) return "•";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return (first + last).toUpperCase();
}

function clampZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(value * 100) / 100));
}

/** `text` with every occurrence of `query` marked (case-insensitive). */
function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const lower = text.toLowerCase();
  const parts: ReactNode[] = [];
  let from = 0;
  for (let at = lower.indexOf(query); at !== -1; at = lower.indexOf(query, from)) {
    if (at > from) parts.push(text.slice(from, at));
    parts.push(<mark key={at}>{text.slice(at, at + query.length)}</mark>);
    from = at + query.length;
  }
  if (from < text.length) parts.push(text.slice(from));
  return <>{parts}</>;
}

function ChevronIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function MinusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12h14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function FitIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function GripIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
      {[6, 12, 18].map((y) => (
        <g key={y} fill="currentColor">
          <circle cx="9" cy={y} r="1.6" />
          <circle cx="15" cy={y} r="1.6" />
        </g>
      ))}
    </svg>
  );
}

/**
 * Organization chart: organization -> departments -> employees, on a
 * pannable, zoomable canvas with search and an employee profile panel.
 *
 * Everything shown is derived from the `departments` and `employees` props,
 * which come straight from the same queries as the management UI below it —
 * so adding, renaming or removing a department, and adding, moving or
 * deactivating an employee, is reflected as soon as those queries refresh.
 * Nothing (organization, department or person) is hardcoded.
 *
 * Only ACTIVE employees appear. The cards show name and title only; contact
 * details (email — never phone) appear only in the profile panel a viewer
 * opens deliberately, and they are the same details the management list on
 * this page already shows that viewer.
 *
 * Employees appear in the order the `employees` prop lists them: the API
 * returns each department's employees in their persisted order, and this
 * component never sorts. When `onReorder` is given (the parent passes it only
 * to users allowed to manage employees — the backend enforces that too), each
 * employee gets a drag handle and can be dragged within their own department
 * (or the Unassigned group); the keyboard alternative is Arrow Up/Down on the
 * handle. `onReorder` receives that group's full new order of employee ids.
 */
export function OrgChart({
  organizationName,
  departments,
  employees,
  onReorder,
  isSavingOrder = false,
}: {
  organizationName: string;
  departments: DepartmentResponse[];
  employees: EmployeeResponse[];
  onReorder?: (departmentId: string | null, orderedEmployeeIds: string[]) => void;
  isSavingOrder?: boolean;
}) {
  const reorderable = onReorder !== undefined;
  const [dragging, setDragging] = useState<DragState | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const levelRef = useRef<HTMLUListElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const handleToFocus = useRef<string | null>(null);
  const reducedMotion = useReducedMotion();
  const cardRefs = useRef(new Map<string, HTMLElement>());
  const lastLayout = useRef<{ order: string; boxes: Map<string, DOMRect>; branchOf: Map<string, string> } | null>(null);

  const [zoom, setZoom] = useState(1);
  const [panning, setPanning] = useState(false);
  const pan = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [query, setQuery] = useState("");
  const [activeMatch, setActiveMatch] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const drawerHeadingRef = useRef<HTMLHeadingElement>(null);
  const cardToRefocus = useRef<string | null>(null);
  const searchId = useId();

  const branches = useMemo<Branch[]>(() => {
    const activeByDepartment = new Map<string | null, EmployeeResponse[]>();
    for (const employee of employees) {
      if (employee.employment_status !== "ACTIVE" || employee.deleted_at) continue;
      const list = activeByDepartment.get(employee.department_id) ?? [];
      list.push(employee);
      activeByDepartment.set(employee.department_id, list);
    }

    const result: Branch[] = departments.map((department, index) => ({
      key: department.id,
      departmentId: department.id,
      name: department.name,
      employees: activeByDepartment.get(department.id) ?? [],
      unassigned: false,
      accent: ACCENTS[index % ACCENTS.length],
    }));
    const unassigned = activeByDepartment.get(null) ?? [];
    if (unassigned.length > 0) {
      result.push({
        key: UNASSIGNED_KEY,
        departmentId: null,
        name: "Unassigned",
        employees: unassigned,
        unassigned: true,
        accent: UNASSIGNED_ACCENT,
      });
    }
    return result;
  }, [departments, employees]);

  const activeEmployees = useMemo(() => branches.flatMap((branch) => branch.employees), [branches]);
  const branchOfEmployee = useMemo(() => {
    const map = new Map<string, Branch>();
    for (const branch of branches) for (const employee of branch.employees) map.set(employee.id, branch);
    return map;
  }, [branches]);
  const selected = selectedId === null ? null : activeEmployees.find((e) => e.id === selectedId) ?? null;

  // --- search ---------------------------------------------------------------
  const term = query.trim().toLowerCase();
  const searching = term.length > 0;
  const search = useMemo(() => {
    const employeeHits = new Set<string>();
    const departmentHits = new Set<string>();
    const branchesWithEmployeeHits = new Set<string>();
    // Node ids in on-screen order, for Enter to step through.
    const order: string[] = [];
    if (term) {
      for (const branch of branches) {
        if (branch.name.toLowerCase().includes(term)) {
          departmentHits.add(branch.key);
          order.push(`dept:${branch.key}`);
        }
        for (const employee of branch.employees) {
          const hay = `${employee.full_name} ${employee.designation ?? ""}`.toLowerCase();
          if (hay.includes(term)) {
            employeeHits.add(employee.id);
            branchesWithEmployeeHits.add(branch.key);
            order.push(`emp:${employee.id}`);
          }
        }
      }
    }
    return { employeeHits, departmentHits, branchesWithEmployeeHits, order };
  }, [branches, term]);
  const matchCount = search.order.length;
  const activeMatchId = searching && matchCount > 0 ? search.order[activeMatch % matchCount] : null;

  // When a department's order changes, cards glide from where they were to
  // where they are now (FLIP) instead of teleporting. Measured after every
  // commit so the "before" positions are always current; animated only for
  // cards that stayed in the same department, and never for reduced motion.
  useLayoutEffect(() => {
    const order = branches.map((b) => `${b.key}:${b.employees.map((e) => e.id).join(",")}`).join("|");
    const boxes = new Map<string, DOMRect>();
    const branchOf = new Map<string, string>();
    cardRefs.current.forEach((node, id) => boxes.set(id, node.getBoundingClientRect()));
    branches.forEach((b) => b.employees.forEach((e) => branchOf.set(e.id, b.key)));

    const previous = lastLayout.current;
    lastLayout.current = { order, boxes, branchOf };
    if (!previous || previous.order === order || reducedMotion) return;

    boxes.forEach((box, id) => {
      const before = previous.boxes.get(id);
      const node = cardRefs.current.get(id);
      if (!before || !node || previous.branchOf.get(id) !== branchOf.get(id)) return;
      if (typeof node.animate !== "function") return;
      const dx = (before.left - box.left) / zoom;
      const dy = (before.top - box.top) / zoom;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      node.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }],
        { duration: 260, easing: "cubic-bezier(0.16, 1, 0.3, 1)" },
      );
    });
  });

  // Reordering moves the card in the DOM, which can drop keyboard focus from
  // the handle that was just used — put it back.
  useEffect(() => {
    const employeeId = handleToFocus.current;
    if (employeeId === null) return;
    handleToFocus.current = null;
    chartRef.current
      ?.querySelector<HTMLElement>(`[data-reorder-handle="${employeeId}"]`)
      ?.focus();
  });

  // The search's current match is brought into view.
  useEffect(() => {
    if (activeMatchId === null) return;
    const node = scrollRef.current?.querySelector<HTMLElement>(`[data-node-id="${activeMatchId}"]`);
    if (node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "nearest", inline: "center", behavior: reducedMotion ? "auto" : "smooth" });
    }
  }, [activeMatchId, reducedMotion]);

  // On first view the whole organization should be visible: a tree a little
  // wider than the canvas opens zoomed to fit (down to 70%, still easily
  // readable); a much larger one opens at 100%, centred on the organization
  // rather than scrolled hard left.
  useLayoutEffect(() => {
    const viewport = scrollRef.current;
    if (!viewport || viewport.scrollWidth <= viewport.clientWidth) return;
    const fit = fittingZoom(1);
    if (fit !== null && fit >= AUTO_FIT_MIN_ZOOM && fit < 1) {
      setZoom(fit);
      requestAnimationFrame(centerHorizontally);
    } else {
      centerHorizontally();
    }
    // Only on mount: afterwards the zoom is the user's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Opening a profile moves focus into it; closing returns focus to the card.
  useEffect(() => {
    if (selectedId !== null) {
      drawerHeadingRef.current?.focus();
      return;
    }
    const id = cardToRefocus.current;
    cardToRefocus.current = null;
    if (id) chartRef.current?.querySelector<HTMLElement>(`[data-employee-card="${id}"]`)?.focus();
  }, [selectedId]);

  // A profile whose employee left the chart (deactivated, removed) closes.
  useEffect(() => {
    if (selectedId !== null && selected === null) setSelectedId(null);
  }, [selectedId, selected]);

  function clearDrag() {
    setDragging(null);
    setDropTarget(null);
  }

  function dropPositionFor(event: DragEvent<HTMLElement>): DropPosition {
    const box = event.currentTarget.getBoundingClientRect();
    return event.clientY < box.top + box.height / 2 ? "before" : "after";
  }

  function handleDragStart(event: DragEvent<HTMLElement>, branch: Branch, employee: EmployeeResponse) {
    setDragging({ branchKey: branch.key, employeeId: employee.id });
    // Firefox only starts a drag when some data is set.
    event.dataTransfer?.setData("text/plain", employee.id);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  }

  function handleDragOver(event: DragEvent<HTMLElement>, branch: Branch, employee: EmployeeResponse) {
    // Employees can only be placed within their own department: over any
    // other list the drop is simply not allowed.
    if (dragging === null || dragging.branchKey !== branch.key) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";

    if (dragging.employeeId === employee.id) {
      if (dropTarget !== null) setDropTarget(null);
      return;
    }
    const position = dropPositionFor(event);
    if (dropTarget?.employeeId !== employee.id || dropTarget.position !== position) {
      setDropTarget({ employeeId: employee.id, position });
    }
  }

  function handleDrop(event: DragEvent<HTMLElement>, branch: Branch, employee: EmployeeResponse) {
    if (dragging === null || dragging.branchKey !== branch.key || onReorder === undefined) return;
    event.preventDefault();

    const currentIds = branch.employees.map((member) => member.id);
    const nextIds = moveEmployeeId(currentIds, dragging.employeeId, employee.id, dropPositionFor(event));
    clearDrag();
    if (nextIds !== currentIds) onReorder(branch.departmentId, nextIds);
  }

  function handleHandleKeyDown(event: KeyboardEvent<HTMLElement>, branch: Branch, index: number) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const neighbour = branch.employees[index + (event.key === "ArrowUp" ? -1 : 1)];
    if (isSavingOrder || neighbour === undefined || onReorder === undefined) return;

    const employee = branch.employees[index];
    const nextIds = moveEmployeeId(
      branch.employees.map((member) => member.id),
      employee.id,
      neighbour.id,
      event.key === "ArrowUp" ? "before" : "after",
    );
    handleToFocus.current = employee.id;
    onReorder(branch.departmentId, nextIds);
  }

  const totalActive = activeEmployees.length;
  const openByDefault = totalActive <= AUTO_EXPAND_LIMIT;
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const isOpen = (key: string) => overrides[key] ?? openByDefault;

  function toggle(key: string) {
    setOverrides((current) => ({ ...current, [key]: !(current[key] ?? openByDefault) }));
  }

  function setAll(open: boolean) {
    setOverrides(Object.fromEntries(branches.map((branch) => [branch.key, open])));
  }

  function selectEmployee(employee: EmployeeResponse) {
    const branch = branchOfEmployee.get(employee.id);
    // Opening someone in a collapsed department (e.g. from a "Reports to"
    // link) expands it, so their card is on the canvas too.
    if (branch && !isOpen(branch.key)) setOverrides((current) => ({ ...current, [branch.key]: true }));
    setSelectedId(employee.id);
  }

  function closeProfile() {
    cardToRefocus.current = selectedId;
    setSelectedId(null);
  }

  // --- zoom & pan -------------------------------------------------------------
  /** Changes the zoom keeping the same point of the chart in the middle of
   * the canvas, instead of drifting towards the top-left. */
  function zoomTo(next: number) {
    const viewport = scrollRef.current;
    const target = clampZoom(next);
    if (!viewport || target === zoom) {
      setZoom(target);
      return;
    }
    const centerX = (viewport.scrollLeft + viewport.clientWidth / 2) / (viewport.scrollWidth || 1);
    const centerY = (viewport.scrollTop + viewport.clientHeight / 2) / (viewport.scrollHeight || 1);
    setZoom(target);
    requestAnimationFrame(() => {
      viewport.scrollLeft = centerX * viewport.scrollWidth - viewport.clientWidth / 2;
      viewport.scrollTop = centerY * viewport.scrollHeight - viewport.clientHeight / 2;
    });
  }

  function zoomBy(delta: number) {
    zoomTo(zoom + delta);
  }

  /** The zoom at which the whole tree fits the canvas (at most 100%), or
   * null when nothing can be measured (e.g. before layout). */
  function fittingZoom(currentZoom: number): number | null {
    const viewport = scrollRef.current;
    const level = levelRef.current;
    const chart = chartRef.current;
    if (!viewport || !level || !chart) return null;
    // The tree's natural (100%) size: measured at the current zoom, then
    // scaled back.
    const width = level.getBoundingClientRect().width / currentZoom;
    const height = chart.getBoundingClientRect().height / currentZoom;
    if (width === 0 || viewport.clientWidth === 0) return null;
    const padding = 48;
    const byWidth = (viewport.clientWidth - padding) / width;
    const byHeight = height > 0 && viewport.clientHeight > 0 ? (viewport.clientHeight - padding) / height : byWidth;
    return clampZoom(Math.min(1, byWidth, byHeight));
  }

  function centerHorizontally() {
    const viewport = scrollRef.current;
    if (viewport) viewport.scrollLeft = (viewport.scrollWidth - viewport.clientWidth) / 2;
  }

  function fitToScreen() {
    const next = fittingZoom(zoom) ?? 1;
    setZoom(next);
    requestAnimationFrame(() => {
      centerHorizontally();
      if (scrollRef.current) scrollRef.current.scrollTop = 0;
    });
  }

  // Dragging the empty canvas pans it (mouse/pen; touch scrolls natively).
  // Cards and controls keep their own behaviour, including drag-to-reorder.
  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || event.pointerType === "touch") return;
    const target = event.target as HTMLElement;
    if (target.closest(".org-node, .org-employee, button, input, a, [role='button']")) return;
    const viewport = scrollRef.current;
    if (!viewport) return;
    pan.current = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
    viewport.setPointerCapture?.(event.pointerId);
    setPanning(true);
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    const start = pan.current;
    const viewport = scrollRef.current;
    if (!start || !viewport) return;
    viewport.scrollLeft = start.left - (event.clientX - start.x);
    viewport.scrollTop = start.top - (event.clientY - start.y);
  }

  function endPan() {
    if (!pan.current) return;
    pan.current = null;
    setPanning(false);
  }

  function handleSectionKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && selectedId !== null) {
      event.preventDefault();
      closeProfile();
    }
  }

  if (branches.length === 0) return null;

  const branchesWithPeople = branches.filter((branch) => branch.employees.length > 0);
  const allOpen = branchesWithPeople.every((branch) => isOpen(branch.key));
  const allClosed = branchesWithPeople.every((branch) => !isOpen(branch.key));
  const departmentCount = departments.length;
  const summary = `${departmentCount} ${departmentCount === 1 ? "department" : "departments"} · ${totalActive} ${
    totalActive === 1 ? "person" : "people"
  }`;

  const directReports = selected ? activeEmployees.filter((e) => e.manager_id === selected.id) : [];
  const manager = selected?.manager_id ? activeEmployees.find((e) => e.id === selected.manager_id) ?? null : null;
  const selectedBranch = selected ? branchOfEmployee.get(selected.id) ?? null : null;

  return (
    <section className="card org-card" aria-labelledby="org-chart-title" onKeyDown={handleSectionKeyDown}>
      <div className="org-chart-header">
        <div className="org-chart-heading">
          <h2 id="org-chart-title">Organization chart</h2>
          <p className="org-chart-summary">{summary}</p>
        </div>
        {reorderable && totalActive > 1 && (
          <span className="org-reorder-hint" role="status">
            {isSavingOrder ? (
              <Spinner label="Saving order…" />
            ) : (
              <>
                <GripIcon />
                Drag employees to reorder them
              </>
            )}
          </span>
        )}
      </div>

      <div className="org-toolbar" role="group" aria-label="Chart controls">
        <div className={`org-search${searching ? " org-search-has-count" : ""}`}>
          <Icon name="search" size={16} className="org-search-icon" />
          <input
            id={searchId}
            type="search"
            value={query}
            placeholder="Search people or departments"
            aria-label="Search employees or departments"
            aria-describedby={`${searchId}-count`}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveMatch(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && matchCount > 0) {
                event.preventDefault();
                setActiveMatch((current) => (current + (event.shiftKey ? matchCount - 1 : 1)) % matchCount);
              } else if (event.key === "Escape" && query) {
                event.preventDefault();
                event.stopPropagation();
                setQuery("");
              }
            }}
          />
          <span id={`${searchId}-count`} className="org-search-count" aria-live="polite">
            {searching ? (matchCount === 0 ? "No matches" : `${matchCount} ${matchCount === 1 ? "match" : "matches"}`) : ""}
          </span>
        </div>

        <div className="org-toolbar-group org-zoom-controls" role="group" aria-label="Zoom">
          <button
            type="button"
            className="org-tool"
            onClick={() => zoomBy(-ZOOM_STEP)}
            disabled={zoom <= MIN_ZOOM}
            aria-label="Zoom out"
            title="Zoom out"
          >
            <MinusIcon />
          </button>
          <button
            type="button"
            className="org-tool org-zoom-level"
            onClick={() => zoomTo(1)}
            aria-label={`Zoom ${Math.round(zoom * 100)}%, reset to 100%`}
            title="Reset to 100%"
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            type="button"
            className="org-tool"
            onClick={() => zoomBy(ZOOM_STEP)}
            disabled={zoom >= MAX_ZOOM}
            aria-label="Zoom in"
            title="Zoom in"
          >
            <Icon name="plus" size={16} />
          </button>
          <button type="button" className="org-tool" onClick={fitToScreen} aria-label="Fit to screen" title="Fit to screen">
            <FitIcon />
          </button>
        </div>

        {totalActive > 0 && (
          <div className="org-toolbar-group" role="group" aria-label="Departments">
            <button type="button" className="org-tool org-tool-text" onClick={() => setAll(true)} disabled={allOpen}>
              Expand all
            </button>
            <button type="button" className="org-tool org-tool-text" onClick={() => setAll(false)} disabled={allClosed}>
              Collapse all
            </button>
          </div>
        )}
      </div>

      <div className={`org-stage${selected ? " org-stage-with-drawer" : ""}`}>
        <div
          className={`org-chart-scroll${panning ? " org-panning" : ""}${searching ? " org-searching" : ""}`}
          ref={scrollRef}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endPan}
          onPointerCancel={endPan}
        >
          <div className="org-chart" ref={chartRef} style={{ zoom }}>
            <div className="org-node org-node-root" aria-label={`Organization: ${organizationName}`}>
              <span className="org-root-mark" aria-hidden="true">
                {initialsOf(organizationName)}
              </span>
              <span className="org-root-text">
                <span className="org-root-eyebrow">Organization</span>
                <span className="org-root-name">{organizationName}</span>
                <span className="org-root-meta">{summary}</span>
              </span>
            </div>

            <ul className="org-level" ref={levelRef} aria-label={`Departments of ${organizationName}`}>
              {branches.map((branch) => {
                const count = branch.employees.length;
                const forcedOpen = searching && search.branchesWithEmployeeHits.has(branch.key);
                const open = count > 0 && (forcedOpen || isOpen(branch.key));
                const listId = `org-employees-${branch.key}`;
                const deptNodeId = `dept:${branch.key}`;
                const deptMatch = search.departmentHits.has(branch.key);
                const searchClass = searching
                  ? deptMatch
                    ? " org-match"
                    : search.branchesWithEmployeeHits.has(branch.key)
                      ? " org-path"
                      : " org-dim"
                  : "";
                const nodeClass = `org-node org-node-dept${branch.unassigned ? " org-node-unassigned" : ""}${searchClass}${
                  activeMatchId === deptNodeId ? " org-match-active" : ""
                }`;
                const label = `${branch.name}, ${employeeCountLabel(count)}`;
                const content = (
                  <>
                    <span className="org-dept-icon" aria-hidden="true">
                      <Icon name={branch.unassigned ? "user" : "team"} size={18} />
                    </span>
                    <span className="org-dept-text">
                      <span className="org-node-title">
                        <Highlight text={branch.name} query={term} />
                      </span>
                      <span className="org-node-meta">{employeeCountLabel(count)}</span>
                    </span>
                    {count > 0 && (
                      <span className="org-dept-chevron" aria-hidden="true">
                        <ChevronIcon />
                      </span>
                    )}
                    {!open && count > 0 && (
                      <span className="org-avatar-stack" aria-hidden="true">
                        {branch.employees.slice(0, AVATAR_PREVIEW).map((employee) => (
                          <span key={employee.id} className="org-avatar org-avatar-xs">
                            {initialsOf(employee.full_name)}
                          </span>
                        ))}
                        {count > AVATAR_PREVIEW && (
                          <span className="org-avatar org-avatar-xs org-avatar-more">+{count - AVATAR_PREVIEW}</span>
                        )}
                      </span>
                    )}
                  </>
                );

                return (
                  <li key={branch.key} className="org-branch" style={{ "--accent": branch.accent } as CSSProperties}>
                    {count > 0 ? (
                      <button
                        type="button"
                        className={nodeClass}
                        data-node-id={deptNodeId}
                        aria-expanded={open}
                        aria-controls={listId}
                        aria-label={label}
                        onClick={() => toggle(branch.key)}
                      >
                        {content}
                      </button>
                    ) : (
                      <div className={nodeClass} data-node-id={deptNodeId} aria-label={label}>
                        {content}
                      </div>
                    )}

                    {open && (
                      <ul
                        id={listId}
                        className={`org-employees${dragging?.branchKey === branch.key ? " org-employees-dropzone" : ""}`}
                        aria-label={`Employees in ${branch.name}`}
                      >
                        {branch.employees.map((employee, index) => {
                          const isDragged = dragging?.employeeId === employee.id;
                          const isSelected = selectedId === employee.id;
                          const empNodeId = `emp:${employee.id}`;
                          const dropClass =
                            dropTarget?.employeeId === employee.id
                              ? ` org-employee-drop-${dropTarget.position}`
                              : "";
                          const matchClass = searching
                            ? search.employeeHits.has(employee.id)
                              ? " org-match"
                              : " org-dim"
                            : "";
                          return (
                            <li
                              key={employee.id}
                              ref={(node) => {
                                if (node) cardRefs.current.set(employee.id, node);
                                else cardRefs.current.delete(employee.id);
                              }}
                              data-node-id={empNodeId}
                              style={{ "--i": index } as CSSProperties}
                              className={`org-employee${reorderable ? " org-employee-reorderable" : ""}${
                                isDragged ? " org-employee-dragging" : ""
                              }${dropClass}${matchClass}${activeMatchId === empNodeId ? " org-match-active" : ""}`}
                              draggable={reorderable ? !isSavingOrder : undefined}
                              onDragStart={
                                reorderable ? (event) => handleDragStart(event, branch, employee) : undefined
                              }
                              onDragOver={
                                reorderable ? (event) => handleDragOver(event, branch, employee) : undefined
                              }
                              onDrop={reorderable ? (event) => handleDrop(event, branch, employee) : undefined}
                              onDragEnd={reorderable ? clearDrag : undefined}
                            >
                              <div
                                role="button"
                                tabIndex={0}
                                className="org-employee-card"
                                data-employee-card={employee.id}
                                aria-pressed={isSelected}
                                aria-haspopup="dialog"
                                onClick={() => (isSelected ? closeProfile() : selectEmployee(employee))}
                                onKeyDown={(event) => {
                                  if (event.key === "Enter" || event.key === " ") {
                                    event.preventDefault();
                                    if (isSelected) closeProfile();
                                    else selectEmployee(employee);
                                  }
                                }}
                              >
                                <span className="org-avatar" aria-hidden="true">
                                  {initialsOf(employee.full_name)}
                                  <span className="org-presence" />
                                </span>
                                <span className="org-employee-text">
                                  <span className="org-node-title">
                                    <Highlight text={employee.full_name} query={term} />
                                  </span>
                                  {employee.designation && (
                                    <span className="org-employee-role">
                                      <Highlight text={employee.designation} query={term} />
                                    </span>
                                  )}
                                </span>
                              </div>
                              {reorderable && (
                                <span
                                  role="button"
                                  tabIndex={0}
                                  className="org-drag-handle"
                                  data-reorder-handle={employee.id}
                                  aria-label={`Reorder ${employee.full_name}`}
                                  aria-disabled={isSavingOrder}
                                  title="Drag to reorder, or press the up/down arrow keys"
                                  onKeyDown={(event) => handleHandleKeyDown(event, branch, index)}
                                >
                                  <GripIcon />
                                </span>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        </div>

        {selected && (
          <aside
            className="org-drawer"
            role="dialog"
            aria-modal="false"
            aria-label="Employee profile"
            style={{ "--accent": selectedBranch?.accent ?? UNASSIGNED_ACCENT } as CSSProperties}
          >
            <div className="org-drawer-top">
              <span className="org-drawer-eyebrow">Employee profile</span>
              <button type="button" className="org-tool" onClick={closeProfile} aria-label="Close profile">
                <Icon name="x" size={16} />
              </button>
            </div>
            <div className="org-drawer-identity">
              <span className="org-avatar org-avatar-lg" aria-hidden="true">
                {initialsOf(selected.full_name)}
                <span className="org-presence" />
              </span>
              <div className="org-drawer-names">
                <h3 ref={drawerHeadingRef} tabIndex={-1}>
                  {selected.full_name}
                </h3>
                {selected.designation && <p className="org-drawer-title">{selected.designation}</p>}
                <span className="org-status-pill">
                  <span className="org-status-dot" aria-hidden="true" />
                  Active
                </span>
              </div>
            </div>

            <dl className="org-drawer-facts">
              <div>
                <dt>Department</dt>
                <dd>{selected.department_name ?? "Unassigned"}</dd>
              </div>
              {(manager || selected.manager_name) && (
                <div>
                  <dt>Reports to</dt>
                  <dd>
                    {manager ? (
                      <button type="button" className="org-person-link" onClick={() => selectEmployee(manager)}>
                        <span className="org-avatar org-avatar-xs" aria-hidden="true">
                          {initialsOf(manager.full_name)}
                        </span>
                        {manager.full_name}
                      </button>
                    ) : (
                      selected.manager_name
                    )}
                  </dd>
                </div>
              )}
              <div>
                <dt>Email</dt>
                <dd>
                  <a className="org-email" href={`mailto:${selected.email}`}>
                    <Icon name="email" size={14} />
                    {selected.email}
                  </a>
                </dd>
              </div>
              {directReports.length > 0 && (
                <div>
                  <dt>Direct reports</dt>
                  <dd className="org-reports">
                    {directReports.map((report) => (
                      <button
                        key={report.id}
                        type="button"
                        className="org-person-link"
                        onClick={() => selectEmployee(report)}
                      >
                        <span className="org-avatar org-avatar-xs" aria-hidden="true">
                          {initialsOf(report.full_name)}
                        </span>
                        {report.full_name}
                      </button>
                    ))}
                  </dd>
                </div>
              )}
            </dl>
          </aside>
        )}
      </div>
    </section>
  );
}
