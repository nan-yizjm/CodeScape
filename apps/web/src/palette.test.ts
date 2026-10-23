import { describe, expect, it } from 'vitest';
import { graphTheme, modulePalette, nodePalette } from './palette';

function luminance(hex: string) {
  const values = hex.slice(1).match(/../g)!.map(value => parseInt(value, 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return values[0] * .2126 + values[1] * .7152 + values[2] * .0722;
}
function contrast(first: string, second: string) {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0] + .05) / (values[1] + .05);
}

describe('画布浅深材质可读性', () => {
  it('六组模块的正文、辅助文字和强调色在浅深外观均达到 4.5:1', () => {
    for (const palette of modulePalette) {
      for (const foreground of [graphTheme.ink, graphTheme.muted, palette.accent]) {
        expect(contrast(foreground, palette.background), `${foreground} / ${palette.background}`).toBeGreaterThanOrEqual(4.5);
      }
      for (const foreground of [graphTheme.darkInk, graphTheme.darkMuted, palette.darkAccent]) {
        expect(contrast(foreground, palette.darkBackground), `${foreground} / ${palette.darkBackground}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
  it('实线在两种画布底色上均有明确对比', () => {
    expect(contrast(graphTheme.edge, graphTheme.background)).toBeGreaterThanOrEqual(3);
    expect(contrast(graphTheme.darkEdge, graphTheme.darkBackground)).toBeGreaterThanOrEqual(3);
  });
  it('未匹配模块和跨周期色调仍取得有效材质', () => {
    expect(nodePalette({tone: -1})).toEqual(modulePalette.at(-1));
    expect(nodePalette({tone: 6})).toEqual(modulePalette[0]);
  });
});
