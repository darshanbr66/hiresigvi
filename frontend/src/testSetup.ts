import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// `findBy*` / `waitFor` default to giving up after 1s. Several screens load a
// chunk lazily (SigviWidget, route pages), and the first test in a file pays
// for Vite transforming that module on demand — 1.2–1.7s on this suite when
// other workers are busy, which made a different handful of tests fail on
// every full run. 4s covers that cold load while staying under the 5s test
// timeout; tests that pass still finish as soon as their element appears.
configure({ asyncUtilTimeout: 4000 });
