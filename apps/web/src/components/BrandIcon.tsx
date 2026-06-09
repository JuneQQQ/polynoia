// Polynoia 品牌图标 — 平台感知（web / macos / ios），无损 SVG（viewBox 0 0 100 100）。
//
// Runtime port of the design handoff `icon-art-v2.jsx` (Polynoia 图标 9版).
// Three concepts × three platform treatments. The static-file equivalents
// (favicon / desktop / mobile / logo masters) are emitted by
// `scripts/gen_brand_icons.py` — keep both in lockstep if you touch the art.
//
// Platform → concept is a PRODUCT decision, NOT baked into this component:
//   web favicon       → mono   ·  desktop/mobile app icon → triad
//   in-app brand logo → triad  (see assets/brand/README.md)
import { useId } from "react";

export type BrandConcept = "mono" | "triad" | "nodes";
export type BrandPlatform = "web" | "macos" | "ios";

// Palette — in lockstep with icon-art-v2.jsx `PC` and gen_brand_icons.py.
const PC = {
	cream: "#f3ede1",
	cream2: "#fbf6ec",
	dark2: "#25201c",
	darkInk: "#15110e",
	orange: "#e96a3c",
	orangeLt: "#f0a07e",
	orangeDk: "#d4552c",
	teal: "#3aab8d",
	violet: "#8a64d8",
} as const;

// —— superellipse path, identical sampling to icon-art-v2.jsx ——
function squirclePath(
	cx: number,
	cy: number,
	r: number,
	n: number,
	steps = 88,
): string {
	const pts: string[] = [];
	for (let i = 0; i <= steps; i++) {
		const t = (i / steps) * 2 * Math.PI;
		const ct = Math.cos(t);
		const st = Math.sin(t);
		const x = cx + r * Math.sign(ct) * Math.abs(ct) ** (2 / n);
		const y = cy + r * Math.sign(st) * Math.abs(st) ** (2 / n);
		pts.push(`${x.toFixed(2)} ${y.toFixed(2)}`);
	}
	return `M${pts.join("L")}Z`;
}
const SQ_MAC = squirclePath(50, 50, 49.4, 4.2); // macOS Big Sur
const SQ_IOS = squirclePath(50, 50, 49.6, 5.2); // iOS continuous corner

// —— the three concept glyphs (fixed 100-box coords) ——
function GlyphMono() {
	return (
		// x=49.4 / y=48 (not 50/53.5) optically centers the "P" — the glyph is
		// stem-heavy with an empty lower-right, so geometric centering reads as
		// low-left. No letterSpacing on a single glyph (it shifted the anchor).
		<text
			x="49.4"
			y="48"
			textAnchor="middle"
			dominantBaseline="central"
			fontFamily="'Inter', system-ui, sans-serif"
			fontWeight="800"
			fontSize="62"
			fill={PC.cream}
		>
			P
		</text>
	);
}

function GlyphTriad() {
	const discs = [
		{ x: 50, y: 39, c: PC.orange },
		{ x: 38, y: 60, c: PC.teal },
		{ x: 62, y: 60, c: PC.violet },
	];
	return (
		<g style={{ isolation: "isolate" }}>
			{discs.map((d) => (
				<circle
					key={d.c}
					cx={d.x}
					cy={d.y}
					r={16.5}
					fill={d.c}
					fillOpacity="0.86"
					style={{ mixBlendMode: "multiply" }}
				/>
			))}
		</g>
	);
}

function GlyphNodes() {
	const nodes = [
		{ x: 32, y: 35, c: PC.orange },
		{ x: 70, y: 39, c: PC.teal },
		{ x: 50, y: 70, c: PC.violet },
	];
	return (
		<g>
			<g
				stroke={PC.cream}
				strokeOpacity="0.22"
				strokeWidth="2"
				strokeLinecap="round"
			>
				<line x1="32" y1="35" x2="70" y2="39" />
				<line x1="70" y1="39" x2="50" y2="70" />
				<line x1="50" y1="70" x2="32" y2="35" />
			</g>
			{nodes.map((a) => (
				<g key={a.c}>
					<circle cx={a.x} cy={a.y} r="8.5" fill={a.c} />
					<circle
						cx={a.x}
						cy={a.y}
						r="8.5"
						fill="none"
						stroke={PC.darkInk}
						strokeWidth="2.5"
					/>
				</g>
			))}
		</g>
	);
}

const CONCEPTS: Record<
	BrandConcept,
	{
		Glyph: () => JSX.Element;
		grad: "orange" | "cream" | "dark";
		flat: string;
		light: boolean;
	}
> = {
	mono: { Glyph: GlyphMono, grad: "orange", flat: PC.orange, light: false },
	triad: { Glyph: GlyphTriad, grad: "cream", flat: PC.cream2, light: true },
	nodes: { Glyph: GlyphNodes, grad: "dark", flat: PC.darkInk, light: false },
};

export interface BrandIconProps {
	concept?: BrandConcept;
	platform?: BrandPlatform;
	size?: number;
	className?: string;
	/** When set, the icon is labelled (role=img + <title>); otherwise decorative. */
	title?: string;
}

export function BrandIcon({
	concept = "mono",
	platform = "macos",
	size = 96,
	className,
	title,
}: BrandIconProps) {
	const uid = useId().replace(/[:]/g, "");
	const c = CONCEPTS[concept];
	const isWeb = platform === "web";
	const path = platform === "ios" ? SQ_IOS : SQ_MAC;
	const gradId = `pn-${c.grad}-${uid}`;
	const sheenId = `pn-sheen-${uid}`;
	const fill = isWeb ? c.flat : `url(#${gradId})`;
	const sheen = !isWeb;
	const ring = platform === "macos";
	const gScale = concept === "triad" ? (platform === "ios" ? 1 : 0.9) : 1;
	const gTransform =
		gScale === 1
			? undefined
			: `translate(50 50) scale(${gScale}) translate(-50 -50)`;

	const Bg = ({
		f,
		stroke,
		sw,
	}: { f: string; stroke?: string; sw?: number }) =>
		isWeb ? (
			<rect
				x="0.6"
				y="0.6"
				width="98.8"
				height="98.8"
				rx="22"
				ry="22"
				fill={f}
				stroke={stroke}
				strokeWidth={sw}
			/>
		) : (
			<path d={path} fill={f} stroke={stroke} strokeWidth={sw} />
		);

	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 100 100"
			className={className}
			style={{ display: "block" }}
			role={title ? "img" : undefined}
			aria-label={title}
			aria-hidden={title ? undefined : true}
		>
			{title && <title>{title}</title>}
			{!isWeb && (
				<defs>
					<linearGradient id={`pn-orange-${uid}`} x1="0" y1="0" x2="0" y2="1">
						<stop offset="0" stopColor={PC.orangeLt} />
						<stop offset="1" stopColor={PC.orangeDk} />
					</linearGradient>
					<linearGradient id={`pn-dark-${uid}`} x1="0" y1="0" x2="0.4" y2="1">
						<stop offset="0" stopColor={PC.dark2} />
						<stop offset="1" stopColor={PC.darkInk} />
					</linearGradient>
					<linearGradient id={`pn-cream-${uid}`} x1="0" y1="0" x2="0" y2="1">
						<stop offset="0" stopColor="#fffdf9" />
						<stop offset="1" stopColor={PC.cream2} />
					</linearGradient>
					<linearGradient id={sheenId} x1="0" y1="0" x2="0" y2="1">
						<stop offset="0" stopColor="#fff" stopOpacity="0.26" />
						<stop offset="0.55" stopColor="#fff" stopOpacity="0" />
					</linearGradient>
				</defs>
			)}
			<Bg f={fill} />
			<g transform={gTransform}>
				<c.Glyph />
			</g>
			{sheen && <Bg f={`url(#${sheenId})`} />}
			{ring && <Bg f="none" stroke="rgba(255,255,255,0.10)" sw={1} />}
			{/* light concept on web: faint inner border so it has an edge on white */}
			{isWeb && c.light && <Bg f="none" stroke="#e3dac6" sw={1.2} />}
		</svg>
	);
}

export default BrandIcon;
