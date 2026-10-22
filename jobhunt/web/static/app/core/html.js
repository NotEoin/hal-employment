/* The one place pages get Preact from: h, render, the html template tag, the hooks and cx(). */
import { h, render, Fragment, createContext } from "preact";
import htm from "htm";

export { h, render, Fragment, createContext };
export { useState, useEffect, useRef, useMemo, useCallback, useContext, useLayoutEffect, useReducer,
  useErrorBoundary, useId } from "preact/hooks";

/** The htm template tag bound to Preact's h. */
export const html = htm.bind(h);

/** Joins the truthy class names with spaces. */
export const cx = (...names) => names.filter(Boolean).join(" ");
