import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { LanguagesInput } from "./LanguagesInput";

function Harness() {
  const [value, setValue] = useState<string[]>([]);
  return <LanguagesInput value={value} onChange={setValue} />;
}

function renderLanguages() {
  return render(<Harness />);
}

function fill(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("LanguagesInput", () => {
describe("languages multi-select", () => {
  const languages = () => screen.getByLabelText(/Languages known/);
  const chips = () =>
    screen.queryAllByRole("listitem").filter((item) => item.classList.contains("lang-chip"));

  it("opens a compact list, adds a language on click and removes it on a second click", () => {
    renderLanguages();
    fireEvent.focus(languages());
    const listbox = screen.getByRole("listbox", { name: "Available languages" });
    expect(listbox).toHaveAttribute("aria-multiselectable", "true");
    expect(languages()).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("option", { name: "Hindi" }));
    expect(chips().map((chip) => chip.textContent)).toEqual(["Hindi"]);
    expect(screen.getByRole("option", { name: "Hindi" })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("option", { name: "Hindi" }));
    expect(chips()).toHaveLength(0);
  });

  it("never adds the same language twice", () => {
    renderLanguages();
    fill(/Languages known/, "English");
    fireEvent.keyDown(languages(), { key: "Enter" });
    // Another spelling of a selected language is not offered as new.
    fill(/Languages known/, "ENGLISH");
    expect(screen.queryByRole("option", { name: /Add/ })).not.toBeInTheDocument();
    // A custom language can be added once; then it isn't offered again.
    fill(/Languages known/, "Sanskrit");
    fireEvent.click(screen.getByRole("option", { name: "Add “Sanskrit”" }));
    fill(/Languages known/, "sanskrit");
    expect(screen.queryByRole("option", { name: /Add/ })).not.toBeInTheDocument();
    expect(chips().map((chip) => chip.textContent)).toEqual(["English", "Sanskrit"]);
  });

  it("works from the keyboard and removes chips", () => {
    renderLanguages();
    fireEvent.focus(languages());
    fireEvent.keyDown(languages(), { key: "ArrowDown" });
    fireEvent.keyDown(languages(), { key: "Enter" }); // Hindi (2nd option)
    expect(chips().map((chip) => chip.textContent)).toEqual(["Hindi"]);
    expect(languages()).toHaveAttribute("aria-activedescendant");

    fireEvent.keyDown(languages(), { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    fireEvent.keyDown(languages(), { key: "Backspace" });
    expect(chips()).toHaveLength(0);

    fireEvent.click(languages()); // reopens the list
    fireEvent.click(screen.getByRole("option", { name: "Tamil" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Tamil" }));
    expect(chips()).toHaveLength(0);
  });
});

});
