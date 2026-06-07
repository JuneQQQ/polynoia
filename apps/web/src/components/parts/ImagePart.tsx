/** ImagePart — renders an image payload(P0: data URL paste from clipboard,
 * P1+: server-hosted URL).
 *
 * Layout: max-width 480px, preserves aspect-ratio via the payload's
 * width/height hint(if absent the image just loads naturally), caption
 * rendered below in muted text.
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
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

  // Esc closes the zoom (the overlay also closes on click).
  useEffect(() => {
    if (!zoomed) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoomed(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomed]);

  return (
    <div className="max-w-[360px]">
      <button
        type="button"
        onClick={() => setZoomed(true)}
        className="block overflow-hidden rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] hover:border-[var(--color-accent)] transition"
        title={`${payload.name || "图片"} · 点击放大`}
      >
        {/* Compact chat thumbnail, unified with the file cards (360px / rounded-xl /
            same border): capped on BOTH axes so a tall screenshot no longer renders
            full-width × proportional height. Click → full overlay. */}
        <img
          src={imgSrc}
          alt={payload.name || "image"}
          width={payload.width ?? undefined}
          height={payload.height ?? undefined}
          loading="lazy"
          className="block max-w-[360px] max-h-[280px] w-auto h-auto object-contain"
        />
      </button>
      {payload.caption && (
        <div className="mt-1 text-[11.5px] text-[var(--color-fg-3)] leading-relaxed">
          {payload.caption}
        </div>
      )}

      {/* Full-screen overlay viewer — PORTALED to <body>. Critical: chat message
          rows carry a `transform` (the anim-fade-up reveal), which would make a
          `position:fixed` overlay rendered in-tree resolve against the ROW, not
          the viewport — so the "fullscreen" preview came out clipped to the
          message box. Portaling escapes that transformed containing block. */}
      {zoomed &&
        createPortal(
          <div
            className="fixed inset-0 z-[100] bg-black/85 grid place-items-center p-6 anim-fade-up"
            role="dialog"
            aria-modal="true"
            onClick={() => setZoomed(false)}
          >
            <img
              src={imgSrc}
              alt={payload.name || "image"}
              // viewport units (not max-h-full): a percentage max-height inside a
              // place-items-center grid resolves against the item's own track →
              // doesn't constrain, so a tall screenshot overflowed the viewport.
              className="max-w-[92vw] max-h-[90vh] w-auto h-auto object-contain rounded-md shadow-2xl"
            />
          </div>,
          document.body,
        )}
    </div>
  );
}
