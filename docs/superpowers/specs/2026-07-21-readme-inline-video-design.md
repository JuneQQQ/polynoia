# README Inline Video Design

**Date:** 2026-07-21
**Status:** Approved direction, awaiting written-spec review
**Scope:** English and Chinese repository READMEs only, plus removal of the now-unused derived poster

## Problem

The README poster currently links to the repository's raw `demo.mp4`. GitHub
redirects that URL to `raw.githubusercontent.com`, which serves the 80,043,319
byte file as `application/octet-stream`. Browsers therefore download it instead
of opening a video player. Linking to the repository blob page is not a viable
fallback: GitHub reports that the 76.3 MB file is too large to preview and emits
no `<video>` element.

The earlier release gate checked that the URL returned successfully, but did
not verify its final media type or actual playback. The fix must test observable
playback, not merely HTTP availability.

## Chosen Approach

Use GitHub's native video-attachment rendering:

1. Create a temporary, browser-compatible derivative of the existing demo.
2. Upload that derivative through GitHub's attachment flow and retain the stable
   `https://github.com/user-attachments/assets/` URL followed by GitHub's issued
   UUID, never its expiring signed S3 redirect.
3. Place the stable attachment URL on its own line in both READMEs. GitHub's
   Markdown renderer converts this form into a native `<video controls>` player.
4. Keep a localized direct-link fallback immediately below the player.

This behavior was verified against an existing public README: its standalone
`user-attachments` URL rendered as a controlled `<video>` element and resolved
to an MP4 media response.

## Video Derivative

The repository's original `assets/readme/demo.mp4` remains unchanged as the
source-quality archive. The uploaded derivative is temporary and is not added
to Git history.

To remain compatible with GitHub's 10 MB free-plan upload ceiling, target:

- duration: the complete 100.334-second demo;
- container: MP4;
- video: H.264, 960x720 (preserving the source's 4:3 aspect ratio), approximately
  620–650 kbit/s;
- audio: AAC, approximately 64 kbit/s;
- size: below 10,000,000 bytes;
- network playback: fast-start metadata at the beginning of the file.

macOS AVFoundation/VideoToolbox provides the local encoding path without adding
a project or system dependency. If the first derivative exceeds the ceiling,
reduce video bitrate only; do not truncate the demo. If the output is visually
unreadable, stop and revisit the hosting choice rather than publishing a broken
demo.

## README Presentation

Replace the centered linked-poster block and raw-video fallback in each README
with:

1. the same standalone stable attachment URL;
2. a short localized fallback link to that same stable URL.

Delete `assets/readme/demo-poster.png`, because it becomes unused. Preserve the
single ImageGen hero, all six original product captures, every product-principle
paragraph, and the original 80 MB demo file.

## Verification Contract

The change is complete only when all of the following hold:

- the pre-fix contract fails because both READMEs still use the raw repository
  URL and GitHub serves it as `application/octet-stream`;
- the uploaded derivative is a complete H.264/AAC MP4 below 10,000,000 bytes;
- the stable attachment URL follows to a `video/mp4` response and does not force
  attachment download;
- English and Chinese contain the identical stable attachment URL and no raw
  `demo.mp4` link or poster reference;
- the rendered GitHub `main` pages each contain one `<video controls>` element;
- in a real browser, invoking play changes `paused` to false and advances
  `currentTime`;
- desktop and 390 px layouts have no document-level overflow;
- type-check, production build, diff hygiene, scope review, and independent
  adversarial review pass before a non-force push to `main`.

## Failure and Rollback

Do not edit the READMEs until the attachment has uploaded and its stable URL has
passed the response-type check. If GitHub does not render or play the attachment,
leave `main` unchanged and fall back to a separately approved GitHub Pages or
external video-hosting design. The raw repository URL must not be restored as a
playback link.
