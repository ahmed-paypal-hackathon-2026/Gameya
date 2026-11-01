/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1"/"true" serves shared/fixtures; "0"/"false" calls the API. Defaults to mocks. */
  readonly VITE_USE_MOCKS?: string;
  /** API origin, with or without the /api/v1 suffix. */
  readonly VITE_API_BASE_URL?: string;
  /** Circle shown by the demo shell. */
  readonly VITE_CIRCLE_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
