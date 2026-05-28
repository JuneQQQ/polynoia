import { clsx, type ClassValue } from "clsx";

/** className combinator. */
export function cn(...inputs: ClassValue[]) {
  return clsx(...inputs);
}
