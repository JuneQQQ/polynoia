/** Conv-scope context provided by ChatPane to message parts that need to
 * call conv-scoped APIs (e.g. DiffPart → /api/diff/apply needs conv_id).
 *
 * Keeps the PARTS_REGISTRY signature simple (just `payload + isStreaming`)
 * while still letting individual parts reach for conv context. The 12
 * parts that don't need it just ignore the context.
 */
import { createContext, useContext } from "react";

type ConvScope = {
  convId: string;
  /** True only when this conv has a workspace_id (workspace-shared git).
   * Some actions (apply diff) only make sense in workspace mode. */
  inWorkspace: boolean;
};

const ConvScopeContext = createContext<ConvScope | null>(null);

export const ConvScopeProvider = ConvScopeContext.Provider;

export function useConvScope(): ConvScope | null {
  return useContext(ConvScopeContext);
}
