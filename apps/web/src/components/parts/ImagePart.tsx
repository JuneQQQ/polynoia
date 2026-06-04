/** ImagePart — renders an image payload(P0: data URL paste from clipboard,
 * P1+: server-hosted URL).
 *
 * Layout: max-width 480px, preserves aspect-ratio via the payload's
 * width/height hint(if absent the image just loads naturally), caption
 * rendered below in muted text.
 */
import { useState } from "react";
import type { ImagePayload } from "../../lib/types";
import { assetUrl } from "../../lib/runtime-config";
import { useStore } from "../../store";

export function ImagePart({ payload }: { payload: ImagePayload }) {
  const [zoomed, setZoomed] = useState(false);
  const imgSrc = assetUrl(payload.src);
  const openPreview = useStore((s) => s.openPreview);

  // Lightweight expand: clicking the thumbnail flips zoomed=true → full
  // overlay. We don't pipe into PreviewPane because images aren't a
  // PreviewPane kind (yet); a future iteration could add "image" preview.
  void openPreview;

  return (
    <div className="max-w-[480px]">
      <button
        type="button"
        onClick={() => setZoomed(true)}
        className="block w-full overflow-hidden rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] hover:border-[var(--color-accent)] transition"
        title={payload.name || "图片"}
      >
        <img
          src={imgSrc}
          alt={payload.name || "image"}
          width={payload.width ?? undefined}
          height={payload.height ?? undefined}
          loading="lazy"
          className="block w-full h-auto"
        />
      </button>
      {payload.caption && (
        <div className="mt-1 text-[11.5px] text-[var(--color-fg-3)] leading-relaxed">
          {payload.caption}
        </div>
      )}

      {/* Full-screen overlay viewer (P0 — minimal, click anywhere to close). */}
      {zoomed && (
        <div
          className="fixed inset-0 z-50 bg-black/80 grid place-items-center p-6 anim-fade-up"
          role="dialog"
          aria-modal="true"
          onClick={() => setZoomed(false)}
        >
          <img
            src={imgSrc}
            alt={payload.name || "image"}
            className="max-w-full max-h-full object-contain rounded-md"
          />
        </div>
      )}
    </div>
  );
}
