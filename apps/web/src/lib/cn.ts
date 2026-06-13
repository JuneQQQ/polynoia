import { type ClassValue, clsx } from "clsx";

/** className combinator. */
export function cn(...inputs: ClassValue[]) {
	return clsx(...inputs);
}
