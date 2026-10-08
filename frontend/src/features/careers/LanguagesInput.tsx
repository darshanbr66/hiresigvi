import { useId, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { Icon } from "../../shared/components/Icon";

/** The suggested languages — any other language can still be typed and
 * added. The backend validates and normalizes every entry
 * (app/schemas/candidate.py::normalize_languages). */
const COMMON_LANGUAGES = [
  "English",
  "Hindi",
  "Tamil",
  "Telugu",
  "Kannada",
  "Malayalam",
  "Marathi",
  "Bengali",
  "Gujarati",
  "Punjabi",
  "Urdu",
  "Odia",
  "French",
  "German",
  "Spanish",
  "Arabic",
  "Japanese",
  "Mandarin",
];

const MAX_LANGUAGES = 15;
/** Mirrors the backend's rule: letters (any script) in words joined by a
 * single space, hyphen, apostrophe or dot; at most 50 characters. */
const LANGUAGE_NAME = /^\p{L}+(?:[ '\-.]\p{L}+)*$/u;

type Option = { kind: "language"; name: string } | { kind: "custom"; name: string };

function normalize(raw: string): string {
  return raw.trim().replace(/\s+/g, " ");
}

/**
 * "Languages known" as a compact multi-select: the selected languages are
 * chips inside the control, and a small listbox popover offers the
 * suggestions. Clicking (or Enter on) a language toggles it; duplicates are
 * impossible (compared case-insensitively). Keyboard: ↑/↓ to move, Enter to
 * toggle, Escape to close, Backspace on an empty search removes the last
 * chip. Typing filters the list and offers to add a language not listed.
 */
export function LanguagesInput({
  value,
  onChange,
  disabled,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const listId = useId();
  const hintId = useId();

  const selectedKeys = new Set(value.map((language) => language.toLowerCase()));
  const isSelected = (name: string) => selectedKeys.has(name.toLowerCase());
  const atLimit = value.length >= MAX_LANGUAGES;

  const term = normalize(query);
  const lowerTerm = term.toLowerCase();
  const options: Option[] = COMMON_LANGUAGES.filter((name) =>
    name.toLowerCase().includes(lowerTerm),
  ).map((name) => ({ kind: "language", name }));
  const canAddCustom =
    term !== "" &&
    term.length <= 50 &&
    LANGUAGE_NAME.test(term) &&
    !COMMON_LANGUAGES.some((name) => name.toLowerCase() === lowerTerm) &&
    !isSelected(term);
  if (canAddCustom) options.push({ kind: "custom", name: term });
  const activeIndex = Math.min(active, Math.max(options.length - 1, 0));

  function toggle(name: string) {
    if (isSelected(name)) {
      onChange(value.filter((language) => language.toLowerCase() !== name.toLowerCase()));
    } else if (!atLimit) {
      onChange([...value, name]);
    }
    setQuery("");
    setActive(0);
  }

  function remove(name: string) {
    onChange(value.filter((language) => language !== name));
    inputRef.current?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) setOpen(true);
        else setActive(Math.min(activeIndex + 1, options.length - 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
        break;
      case "Enter": {
        event.preventDefault();
        const option = options[activeIndex];
        if (option) toggle(option.name);
        break;
      }
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        }
        break;
      case "Backspace":
        if (query === "" && value.length > 0) onChange(value.slice(0, -1));
        break;
    }
  }

  function handleBlur(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setOpen(false);
      setQuery("");
    }
  }

  const optionId = (index: number) => `${listId}-option-${index}`;

  return (
    <div className="field">
      <label htmlFor={inputId}>
        Languages known <span className="required-mark" aria-hidden="true">*</span>
      </label>
      <div className="lang-select" onBlur={handleBlur}>
        <div
          className={`lang-control${open ? " lang-control-open" : ""}${disabled ? " lang-control-disabled" : ""}`}
          onMouseDown={(event) => {
            // Clicking the empty space of the control focuses the search.
            if (event.target === event.currentTarget) {
              event.preventDefault();
              inputRef.current?.focus();
              setOpen(true);
            }
          }}
        >
          {value.length > 0 && (
            <ul className="lang-chips" aria-label="Selected languages">
              {value.map((language) => (
                <li key={language} className="lang-chip">
                  {language}
                  <button
                    type="button"
                    className="chip-remove"
                    onClick={() => remove(language)}
                    disabled={disabled}
                    aria-label={`Remove ${language}`}
                  >
                    <Icon name="x" size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <input
            ref={inputRef}
            id={inputId}
            className="lang-search"
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={open && options.length > 0 ? optionId(activeIndex) : undefined}
            aria-describedby={hintId}
            aria-required="true"
            autoComplete="off"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
            onKeyDown={handleKeyDown}
            placeholder={value.length === 0 ? "Select languages" : "Add more"}
            disabled={disabled}
          />
          <svg className="lang-caret" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        {open && !disabled && (
          <ul id={listId} role="listbox" aria-multiselectable="true" aria-label="Available languages" className="lang-menu">
            {options.length === 0 && (
              <li className="lang-empty" role="presentation">
                No matching language
              </li>
            )}
            {options.map((option, index) => {
              const selected = option.kind === "language" && isSelected(option.name);
              const unavailable = !selected && atLimit;
              return (
                <li
                  key={`${option.kind}-${option.name}`}
                  id={optionId(index)}
                  role="option"
                  aria-selected={selected}
                  aria-disabled={unavailable || undefined}
                  className={`lang-option${index === activeIndex ? " lang-option-active" : ""}${selected ? " lang-option-selected" : ""}`}
                  // Keep focus in the search box while clicking an option.
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => !unavailable && toggle(option.name)}
                >
                  {option.kind === "custom" ? (
                    <span>Add “{option.name}”</span>
                  ) : (
                    <>
                      <span>{option.name}</span>
                      {selected && <Icon name="check" size={14} />}
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <span id={hintId} className="field-hint">
        {atLimit
          ? `You can add up to ${MAX_LANGUAGES} languages.`
          : "Select all that apply. Type to search, or to add a language not listed."}
      </span>
    </div>
  );
}
