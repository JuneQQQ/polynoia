# README Inline Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the download-only README demo link with one GitHub-native inline video player shared by the English and Chinese READMEs.

**Architecture:** Produce a temporary sub-10 MB H.264/AAC derivative with macOS AVFoundation and upload it to GitHub's user-attachment store. Put the stable attachment URL on its own line in each README so GitHub renders `<video controls>`, retain a direct-link fallback, and delete the now-unused derived poster while preserving the original source video.

**Tech Stack:** GitHub Flavored Markdown, GitHub user attachments, macOS AVFoundation/VideoToolbox, Swift 6, shell contracts, pnpm 9, Vite, and real GitHub browser verification.

## Global Constraints

- Keep `assets/readme/demo.mp4` byte-for-byte unchanged.
- Preserve the sole ImageGen hero and all six original product captures.
- Upload a complete 100.334-second derivative; never truncate the demo.
- The derivative must be H.264/AAC MP4, 960x720, below 10,000,000 bytes, and fast-start.
- Store only GitHub's stable `github.com/user-attachments/assets/` URL in the READMEs, never an expiring signed S3 URL.
- English and Chinese must use the identical attachment URL.
- Do not add the compressed derivative to Git history.
- Do not force-push.

---

### Task 1: Produce and upload a playable GitHub video attachment

**Files:**
- Create temporarily: `/tmp/polynoia-demo-transcode.swift`
- Create temporarily: `/tmp/polynoia-demo-transcode`
- Create temporarily: `/tmp/polynoia-demo-720p.mp4`
- Create temporarily: `/tmp/polynoia-readme-video-url`
- Create locally, ignored: `.superpowers/sdd/verify-readme-inline-video.sh`

**Interfaces:**
- Consumes: `assets/readme/demo.mp4` and authenticated GitHub browser state.
- Produces: `/tmp/polynoia-readme-video-url`, containing one stable attachment URL followed by a newline.

- [x] **Step 1: Write the failing playback contract.**

Create `.superpowers/sdd/verify-readme-inline-video.sh` with executable mode and this exact content:

```sh
#!/bin/sh
set -eu

extract_url() {
  rg -o '^https://github\.com/user-attachments/assets/[0-9A-Za-z-]+$' "$1"
}

en_url=$(extract_url README.md)
zh_url=$(extract_url README.zh-CN.md)
test -n "$en_url"
test "$en_url" = "$zh_url"

for readme in README.md README.zh-CN.md; do
  test "$(rg -F -c "$en_url" "$readme")" -eq 2
  ! rg -q 'github\.com/JuneQQQ/polynoia/raw/.*/assets/readme/demo\.mp4' "$readme"
  ! rg -q 'assets/readme/demo-poster\.png' "$readme"
done

headers=$(mktemp /tmp/polynoia-video-headers.XXXXXX)
trap 'rm -f "$headers"' EXIT
curl --fail --silent --show-error --location --range 0-0 \
  --dump-header "$headers" --output /dev/null "$en_url"
media_type=$(awk 'tolower($1)=="content-type:" {gsub("\\r", "", $2); value=$2} END{print value}' "$headers")
disposition=$(awk 'tolower($1)=="content-disposition:" {sub(/^[^:]+:[[:space:]]*/, ""); gsub("\\r", ""); value=$0} END{print value}' "$headers")
test "$media_type" = 'video/mp4'
case "$disposition" in
  *attachment*) exit 1 ;;
esac
```

- [x] **Step 2: Run the contract and verify RED.**

Run:

```bash
chmod +x .superpowers/sdd/verify-readme-inline-video.sh
.superpowers/sdd/verify-readme-inline-video.sh
```

Expected: FAIL because neither README contains a standalone stable user-attachment URL; the current raw URL resolves as `application/octet-stream`.

- [x] **Step 3: Create the exact AVFoundation transcoder.**

Create `/tmp/polynoia-demo-transcode.swift` with:

```swift
import AVFoundation
import Foundation
import VideoToolbox

func fail(_ text: String, _ code: Int = 1) -> NSError {
    NSError(
        domain: "PolynoiaDemoTranscode",
        code: code,
        userInfo: [NSLocalizedDescriptionKey: text]
    )
}

@main
struct PolynoiaDemoTranscode {
    static func main() async throws {
        guard CommandLine.arguments.count == 3 else {
            throw fail("usage: polynoia-demo-transcode input.mp4 output.mp4", 64)
        }
        let source = URL(fileURLWithPath: CommandLine.arguments[1])
        let destination = URL(fileURLWithPath: CommandLine.arguments[2])
        guard !FileManager.default.fileExists(atPath: destination.path) else {
            throw fail("refusing to overwrite \(destination.path)")
        }

        let asset = AVURLAsset(url: source)
        let duration = try await asset.load(.duration)
        guard let videoTrack = try await asset.loadTracks(withMediaType: .video).first,
              let audioTrack = try await asset.loadTracks(withMediaType: .audio).first else {
            throw fail("source needs video and audio")
        }
        let size = try await videoTrack.load(.naturalSize)
        let transform = try await videoTrack.load(.preferredTransform)
        guard size == CGSize(width: 2880, height: 2160), transform == .identity else {
            throw fail("unexpected source geometry: \(size), \(transform)")
        }

        let reader = try AVAssetReader(asset: asset)
        let writer = try AVAssetWriter(outputURL: destination, fileType: .mp4)
        writer.shouldOptimizeForNetworkUse = true

        let composition = AVMutableVideoComposition()
        composition.renderSize = CGSize(width: 960, height: 720)
        composition.frameDuration = CMTime(value: 1, timescale: 20)
        let instruction = AVMutableVideoCompositionInstruction()
        instruction.timeRange = CMTimeRange(start: .zero, duration: duration)
        let layer = AVMutableVideoCompositionLayerInstruction(assetTrack: videoTrack)
        layer.setTransform(CGAffineTransform(scaleX: 1.0 / 3.0, y: 1.0 / 3.0), at: .zero)
        instruction.layerInstructions = [layer]
        composition.instructions = [instruction]

        let videoOutput = AVAssetReaderVideoCompositionOutput(
            videoTracks: [videoTrack],
            videoSettings: [
                kCVPixelBufferPixelFormatTypeKey as String:
                    Int(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)
            ]
        )
        videoOutput.videoComposition = composition
        videoOutput.alwaysCopiesSampleData = false
        let audioOutput = AVAssetReaderTrackOutput(
            track: audioTrack,
            outputSettings: [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsNonInterleaved: false,
            ]
        )
        audioOutput.alwaysCopiesSampleData = false

        let videoSettings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: 960,
            AVVideoHeightKey: 720,
            AVVideoEncoderSpecificationKey: [
                kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder as String: true
            ],
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: 620_000,
                kVTCompressionPropertyKey_DataRateLimits as String: [82_000, 1],
                AVVideoExpectedSourceFrameRateKey: 20,
                AVVideoMaxKeyFrameIntervalKey: 40,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
                AVVideoH264EntropyModeKey: AVVideoH264EntropyModeCABAC,
            ],
        ]
        let audioSettings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 2,
            AVEncoderBitRateKey: 64_000,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        guard writer.canApply(outputSettings: videoSettings, forMediaType: .video),
              writer.canApply(outputSettings: audioSettings, forMediaType: .audio) else {
            throw fail("H.264/AAC settings unsupported")
        }

        let videoInput = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
        let audioInput = AVAssetWriterInput(mediaType: .audio, outputSettings: audioSettings)
        videoInput.expectsMediaDataInRealTime = false
        audioInput.expectsMediaDataInRealTime = false
        guard reader.canAdd(videoOutput), reader.canAdd(audioOutput),
              writer.canAdd(videoInput), writer.canAdd(audioInput) else {
            throw fail("cannot connect reader and writer")
        }
        reader.add(videoOutput)
        reader.add(audioOutput)
        writer.add(videoInput)
        writer.add(audioInput)

        guard writer.startWriting() else {
            throw writer.error ?? fail("writer start failed")
        }
        writer.startSession(atSourceTime: .zero)
        guard reader.startReading() else {
            throw reader.error ?? fail("reader start failed")
        }

        var videoDone = false
        var audioDone = false
        while !videoDone || !audioDone {
            var moved = false
            if !videoDone && videoInput.isReadyForMoreMediaData {
                if let sample = videoOutput.copyNextSampleBuffer() {
                    guard videoInput.append(sample) else {
                        throw writer.error ?? fail("video append failed")
                    }
                    moved = true
                } else {
                    videoInput.markAsFinished()
                    videoDone = true
                }
            }
            if !audioDone && audioInput.isReadyForMoreMediaData {
                if let sample = audioOutput.copyNextSampleBuffer() {
                    guard audioInput.append(sample) else {
                        throw writer.error ?? fail("audio append failed")
                    }
                    moved = true
                } else {
                    audioInput.markAsFinished()
                    audioDone = true
                }
            }
            if reader.status == .failed {
                throw reader.error ?? fail("reader failed")
            }
            if writer.status == .failed {
                throw writer.error ?? fail("writer failed")
            }
            if !moved {
                try await Task.sleep(for: .milliseconds(1))
            }
        }

        await writer.finishWriting()
        guard writer.status == .completed else {
            throw writer.error ?? fail("finish failed")
        }
        let bytes = try destination.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard bytes < 10_000_000 else {
            throw fail("output is at least 10 MB: \(bytes)", 2)
        }
        print("wrote \(destination.path): \(bytes) bytes")
    }
}
```

- [x] **Step 4: Compile, run, and validate the derivative.**

Run:

```bash
xcrun swiftc -typecheck -parse-as-library /tmp/polynoia-demo-transcode.swift
xcrun swiftc -O -parse-as-library /tmp/polynoia-demo-transcode.swift -o /tmp/polynoia-demo-transcode
/tmp/polynoia-demo-transcode assets/readme/demo.mp4 /tmp/polynoia-demo-720p.mp4
test "$(stat -f %z /tmp/polynoia-demo-720p.mp4)" -lt 10000000
mdls -name kMDItemDurationSeconds -name kMDItemPixelWidth -name kMDItemPixelHeight \
  -name kMDItemCodecs -name kMDItemTotalBitRate /tmp/polynoia-demo-720p.mp4
```

Then verify top-level atom order:

```bash
python3 - /tmp/polynoia-demo-720p.mp4 <<'PY'
import struct
import sys

atoms = []
with open(sys.argv[1], "rb") as stream:
    while True:
        header = stream.read(8)
        if len(header) < 8:
            break
        size, kind = struct.unpack(">I4s", header)
        header_size = 8
        if size == 1:
            size = struct.unpack(">Q", stream.read(8))[0]
            header_size = 16
        if size == 0:
            break
        atoms.append(kind.decode("ascii", "replace"))
        stream.seek(size - header_size, 1)
print(atoms)
assert "moov" in atoms and "mdat" in atoms
assert atoms.index("moov") < atoms.index("mdat"), atoms
PY
```

Expected: complete duration near `100.334`, H.264/AAC, `960x720`, fewer than 10,000,000 bytes, and `moov` before `mdat`.

- [x] **Step 5: Upload without creating an issue.**

Open `https://github.com/JuneQQQ/polynoia/issues/new` in the authenticated browser, attach `/tmp/polynoia-demo-720p.mp4` to the issue body, wait for GitHub to replace the upload placeholder with a stable `https://github.com/user-attachments/assets/` URL, and do not submit the issue. Save only that stable URL plus a newline to `/tmp/polynoia-readme-video-url`.

Expected: the draft issue is never created; GitHub has uploaded the media and issued one stable URL.

- [x] **Step 6: Verify the attachment before touching README.**

Run:

```bash
video_url=$(cat /tmp/polynoia-readme-video-url)
case "$video_url" in
  https://github.com/user-attachments/assets/*) ;;
  *) exit 1 ;;
esac
headers=$(mktemp /tmp/polynoia-upload-headers.XXXXXX)
curl --fail --silent --show-error --location --range 0-0 \
  --dump-header "$headers" --output /dev/null "$video_url"
awk 'tolower($1)=="content-type:" {gsub("\r", "", $2); value=$2} END{print value}' "$headers" | grep -Fx video/mp4
! rg -i '^content-disposition:.*attachment' "$headers"
rm "$headers"
```

Expected: the stable attachment follows to a partial `video/mp4` response and does not force download.

### Task 2: Replace poster links with the native inline player

**Files:**
- Modify: `README.md:28-35`
- Modify: `README.zh-CN.md:28-35`
- Delete: `assets/readme/demo-poster.png`
- Verify locally: `.superpowers/sdd/verify-readme-inline-video.sh`

**Interfaces:**
- Consumes: the stable URL stored in `/tmp/polynoia-readme-video-url`.
- Produces: two READMEs with identical standalone attachment URLs and localized direct-link fallbacks.

- [x] **Step 1: Replace both demo blocks with the native attachment form.**

Read `video_url=$(cat /tmp/polynoia-readme-video-url)`. Replace the English poster block with exactly:

```markdown
$video_url

<p align="center">
  <sub>▶︎ If the player does not start, <a href="$video_url">open the video directly</a>.</sub>
</p>
```

Replace the Chinese poster block with exactly:

```markdown
$video_url

<p align="center">
  <sub>▶︎ 如果播放器未启动，请<a href="$video_url">直接打开视频</a>。</sub>
</p>
```

The literal value from `/tmp/polynoia-readme-video-url`, not the text `$video_url`, must be written in all four locations.

- [x] **Step 2: Delete the unused poster and verify GREEN.**

Run:

```bash
git rm assets/readme/demo-poster.png
.superpowers/sdd/verify-readme-inline-video.sh
git diff --check
```

Expected: the contract passes; there are no raw demo links or poster references.

- [x] **Step 3: Verify protected media and bilingual parity.**

Run:

```bash
git diff --quiet main...HEAD -- assets/readme/demo.mp4 \
  assets/readme/community/hero-shared-studio.webp \
  assets/readme/群聊与编排.png assets/readme/预览.png assets/readme/diff.png \
  assets/readme/联系人.png assets/readme/质量面板.jpg assets/readme/角色库.jpg
git diff --quiet -- assets/readme/demo.mp4 \
  assets/readme/community/hero-shared-studio.webp \
  assets/readme/群聊与编排.png assets/readme/预览.png assets/readme/diff.png \
  assets/readme/联系人.png assets/readme/质量面板.jpg assets/readme/角色库.jpg
git diff --cached --quiet -- assets/readme/demo.mp4 \
  assets/readme/community/hero-shared-studio.webp \
  assets/readme/群聊与编排.png assets/readme/预览.png assets/readme/diff.png \
  assets/readme/联系人.png assets/readme/质量面板.jpg assets/readme/角色库.jpg
test "$(rg -c '^## ' README.md)" = "$(rg -c '^## ' README.zh-CN.md)"
git status -sb
```

Expected: protected files are unchanged and only the approved README/player scope is dirty.

- [x] **Step 4: Commit the README playback fix.**

```bash
git add README.md README.zh-CN.md assets/readme/demo-poster.png
git commit -m "fix(docs): play product demo inline"
```

### Task 3: Render, adversarially review, and publish

**Files:**
- Verify: `README.md`
- Verify: `README.zh-CN.md`
- Verify: `assets/readme/demo.mp4`
- Modify: `docs/superpowers/plans/2026-07-21-readme-inline-video.md`

**Interfaces:**
- Consumes: the committed bilingual inline-player change.
- Produces: a verified non-force update to `main`.

- [x] **Step 1: Run project and diff gates.**

```bash
pnpm --filter @polynoia/web exec tsc --noEmit
pnpm --filter @polynoia/web build
.superpowers/sdd/verify-readme-inline-video.sh
git diff --check main...HEAD
git diff --name-status main...HEAD
git status -sb
```

Expected: all commands exit zero and the diff contains only the spec, plan, two READMEs, and poster deletion.

- [x] **Step 2: Push the feature branch and test rendered playback.**

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git push -u origin agent/readme-inline-video
```

Open both branch READMEs on GitHub at desktop and `390x844`. For each page verify:

- exactly one `article.markdown-body video` exists and has `controls=true`;
- `video.currentSrc` resolves to GitHub's user-asset storage;
- clicking the video is a trusted playback gesture;
- `paused` changes to false and `currentTime` advances by at least 0.5 seconds;
- the direct fallback opens a native media response rather than downloading;
- the document does not overflow horizontally and the six-capture grid remains intact;
- the console has no new warning or error.

- [x] **Step 3: Request independent adversarial review.**

Ask a read-only reviewer to inspect `main...HEAD`, the design, this plan, the response-header evidence, video metadata/atom evidence, and both browser renders. Fix every Critical or Important finding and rerun Steps 1–2.

Expected: Critical 0, Important 0, and `Ready to merge: Yes`.

- [x] **Step 4: Record the exact release gate and commit the plan.**

Mark completed steps and record derivative bytes/codecs/duration, stable attachment URL, response type, browser playback timing, viewport widths, build result, and review result. Then run:

```bash
git add docs/superpowers/plans/2026-07-21-readme-inline-video.md
git commit -m "docs: record README video verification"
```

Observed feature-branch release gate (2026-07-21, Asia/Shanghai):

- The complete derivative is `5,110,518` bytes: H.264 `960x720` at 20 fps
  (`100.350s`) plus AAC stereo at 44.1 kHz (`100.333s`). Its top-level MP4
  atoms are `ftyp`, `moov`, `mdat`, so it is fast-start. The derivative was
  kept under `/tmp` and never entered Git; the original 80,043,319-byte video
  retained its original blob.
- GitHub now keeps unsubmitted issue-draft attachments authentication-gated:
  the draft UUID returned anonymous `404` despite being complete for the
  signed-in user. To avoid creating an issue, the same derivative was uploaded
  through the README editor on `agent/readme-inline-video` and persisted by the
  exact final English player block. The stable public URL is
  `https://github.com/user-attachments/assets/993fd4d4-a535-4900-9074-e93d28037e47`.
- A fresh anonymous ranged request follows to `206 Partial Content` with
  `Content-Type: video/mp4`, `Content-Range: bytes 0-0/5110518`, and no
  attachment content disposition. The localized fallback opens a native media
  document instead of starting a download.
- `pnpm --filter @polynoia/web exec tsc --noEmit`, the production build, the
  playback contract, and full-branch diff hygiene all pass. The Vite build
  completed in `9.22s`; its existing chunk-size and mixed-import notices are
  informational.
- Real GitHub branch playback passed at desktop `1440x900` and mobile
  `390x844`: English advanced `1.101242s` and `1.346155s`; Chinese advanced
  `1.092338s` and `1.405631s`. Every page rendered exactly one controlled
  video, had no document-level horizontal overflow, retained all six product
  captures, and added no playback warning or error. GitHub Primer and installed
  Chrome-extension diagnostics were recorded separately as host noise.
- Per-task and whole-branch adversarial reviews report Critical `0`, Important
  `0`; the final reviewer returned `Ready to merge: Yes`.

- [x] **Step 5: Fast-forward and push `main` without force.**

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git -C /Users/lishaobo/governance-center/polynoia merge --ff-only agent/readme-inline-video
git -C /Users/lishaobo/governance-center/polynoia push origin main
```

After the push, open the actual `main` README and repeat the real playback timing check. Verify local `main`, `origin/main`, and `agent/readme-inline-video` resolve to the same commit.

Observed published-main gate:

- `main` was updated by ordinary fast-forward push; no force-push occurred.
- On the real repository root, the English video advanced from `0` to
  `1.463251s` after a trusted click. On the explicit Chinese `main` README, it
  advanced from `0` to `1.49602s`.
- Both published pages contain exactly one controlled video whose source is a
  GitHub user-asset host. Both direct fallbacks open a native `video/mp4`
  document and do not start a download.
- At the playback check, local `main`, `origin/main`, the local feature branch,
  and its remote ref all resolved to
  `d4c89c4c829c56a043ed40a5c9a23c05332ca9e5` with clean working trees. This
  documentation-only completion commit is then fast-forwarded through the same
  feature-to-main path.
