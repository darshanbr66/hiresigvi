/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** Careers-site slug the public homepage presents (features/public/careersOrg.ts). */
  readonly VITE_CAREERS_ORG_SLUG?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
