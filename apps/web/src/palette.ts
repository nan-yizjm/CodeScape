/** 展示色只影响材质；浅色导出与实时画布共享同一份色值。 */
export const modulePalette = [
  { background: '#edf0f7', accent: '#415b85', darkBackground: '#20242d', darkAccent: '#a9bce1' },
  { background: '#f0edf5', accent: '#6b5484', darkBackground: '#26222d', darkAccent: '#c7b3df' },
  { background: '#f5efe4', accent: '#806236', darkBackground: '#2b261d', darkAccent: '#ddc091' },
  { background: '#eaf1ec', accent: '#3d6953', darkBackground: '#1e2823', darkAccent: '#a5cbb4' },
  { background: '#f4ecef', accent: '#875369', darkBackground: '#2d2227', darkAccent: '#dfb0c2' },
  { background: '#eaf0f2', accent: '#3e6573', darkBackground: '#1e272b', darkAccent: '#a5c8d4' },
] as const;

export const graphTheme = {
  background: '#f5f5f3', surface: '#fcfcfb', edge: '#788078', internalEdge: '#93998e',
  ink: '#262824', muted: '#5e635b', dot: '#ddded7', nodeRadius: 16,
  darkBackground: '#151515', darkSurface: '#20201e', darkEdge: '#9caa99', darkInternalEdge: '#808b7c',
  darkInk: '#e9e9e3', darkMuted: '#b5b9af', darkDot: '#2b2d28',
} as const;

export function nodePalette(node: { tone?: number }) {
  const index = ((node.tone ?? 0) % modulePalette.length + modulePalette.length) % modulePalette.length;
  return modulePalette[index];
}

/** CSS 色值会随系统外观即时切换，不触发布局或源码编辑器重建。 */
export const graphThemeVariables = {
  '--graph-background-light': graphTheme.background, '--graph-background-dark': graphTheme.darkBackground,
  '--graph-surface-light': graphTheme.surface, '--graph-surface-dark': graphTheme.darkSurface,
  '--graph-edge-light': graphTheme.edge, '--graph-edge-dark': graphTheme.darkEdge,
  '--graph-internal-edge-light': graphTheme.internalEdge, '--graph-internal-edge-dark': graphTheme.darkInternalEdge,
  '--graph-ink-light': graphTheme.ink, '--graph-ink-dark': graphTheme.darkInk,
  '--graph-muted-light': graphTheme.muted, '--graph-muted-dark': graphTheme.darkMuted,
  '--graph-dot-light': graphTheme.dot, '--graph-dot-dark': graphTheme.darkDot,
} as const;
