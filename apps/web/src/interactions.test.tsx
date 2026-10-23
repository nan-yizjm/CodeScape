import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { CopyButton, Disclosure, SoftReveal, revealCharacters } from './InteractionPrimitives';
import { createCopyFeedback, sourceExcerpt } from './clipboardFeedback';
import { springProgress } from './motion';
import { DragHandle, panelDragWidth } from './DragHandle';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('可访问的渐进折叠与文字反馈', () => {
  it('收起时内容仍可供 Grid 动画使用，但从焦点和无障碍树中隔离', () => {
    const markup = renderToStaticMarkup(<Disclosure title="源码依据"><button>打开源码</button></Disclosure>);
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain('aria-hidden="true" inert=""');
    expect(markup).toContain('打开源码');
    const control = markup.match(/aria-controls="([^"]+)"/)?.[1];
    expect(control).toBeTruthy();
    expect(markup).toContain(`id="${control}"`);
  });
  it('受控展开恢复持久化状态，内容可访问', () => {
    const markup = renderToStaticMarkup(<Disclosure title="源码依据" open><button>打开源码</button></Disclosure>);
    expect(markup).toContain('aria-expanded="true"');
    expect(markup).toContain('aria-hidden="false"');
    expect(markup).not.toContain('inert=');
  });
  it('按完整字素显示中文、组合字和 emoji，屏幕阅读器获得完整句子', () => {
    expect(revealCharacters('已存 e\u0301 👩‍💻')).toEqual(['已', '存', ' ', 'e\u0301', ' ', '👩‍💻']);
    const markup = renderToStaticMarkup(<SoftReveal text="已经保存" fresh />);
    expect(markup).toContain('interaction-sr-only">已经保存');
    expect(markup).toContain('--reveal-delay:90ms');
    expect(markup).toContain('aria-hidden="true"');
  });
  it('历史内容、长内容和减少动画偏好不逐字渲染', () => {
    expect(renderToStaticMarkup(<SoftReveal text="已完成的说明" />)).not.toContain('soft-reveal-character');
    expect(renderToStaticMarkup(<SoftReveal text={'字'.repeat(49)} fresh />)).not.toContain('soft-reveal-character');
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    expect(renderToStaticMarkup(<SoftReveal text="已经保存" fresh />)).not.toContain('soft-reveal-character');
  });
});

describe('复制反馈生命周期', () => {
  it('只在写入成功后确认，在 2.5 秒时淡出，160ms 后恢复', async () => {
    vi.useFakeTimers();
    const changed = vi.fn(); const clipboard = vi.fn().mockResolvedValue(undefined);
    const feedback = createCopyFeedback(changed);
    await feedback.copy('源码原文', clipboard);
    expect(clipboard).toHaveBeenCalledWith('源码原文');
    expect(changed.mock.calls.map(call => call[0])).toEqual(['copying', 'success']);
    vi.advanceTimersByTime(2499); expect(changed).toHaveBeenLastCalledWith('success');
    vi.advanceTimersByTime(1); expect(changed).toHaveBeenLastCalledWith('fading');
    vi.advanceTimersByTime(160); expect(changed).toHaveBeenLastCalledWith('idle');
    feedback.dispose();
  });
  it('重复复制重新计时；失败没有成功对勾，并可复制同一正文重试', async () => {
    vi.useFakeTimers();
    const changed = vi.fn(); const feedback = createCopyFeedback(changed);
    await feedback.copy('保留内容', () => Promise.reject(new Error('denied')));
    expect(changed).toHaveBeenLastCalledWith('error');
    expect(changed).not.toHaveBeenCalledWith('success');
    const clipboard = vi.fn().mockResolvedValue(undefined);
    await feedback.copy('保留内容', clipboard);
    vi.advanceTimersByTime(2000);
    await feedback.copy('保留内容', clipboard);
    vi.advanceTimersByTime(1000); expect(changed).toHaveBeenLastCalledWith('success');
    vi.advanceTimersByTime(1500); expect(changed).toHaveBeenLastCalledWith('fading');
    feedback.dispose();
  });
  it('面板切换后忽略未完成复制，清除旧内容反馈和计时器', async () => {
    vi.useFakeTimers();
    let complete!: () => void;
    const changed = vi.fn(); const feedback = createCopyFeedback(changed);
    const pending = feedback.copy('旧快照', () => new Promise<void>(resolve => { complete = resolve; }));
    feedback.dispose(); complete(); await pending;
    vi.runAllTimers(); expect(changed.mock.calls.map(call => call[0])).toEqual(['copying']);
  });
  it('复制仅包含快照指定行，保留源码行末；空文本入口不可用', () => {
    expect(sourceExcerpt('first\r\nsecond\r\nthird', 2, 3)).toBe('second\r\nthird');
    expect(sourceExcerpt('first\nsecond\n', 1, 1)).toBe('first\n');
    expect(sourceExcerpt('', 1, 1)).toBe('');
    expect(renderToStaticMarkup(<CopyButton text="" label="复制片段" />)).toContain('disabled=""');
  });
});

describe('解析弹簧参数', () => {
  it('1200/80 始终单调趋近目标，没有回弹', () => {
    let previous = 0;
    for (let frame = 0; frame <= 100; frame++) {
      const progress = springProgress(frame / 100, 1200, 80);
      expect(progress).toBeGreaterThanOrEqual(previous);
      expect(progress).toBeLessThanOrEqual(1);
      previous = progress;
    }
    expect(previous).toBeCloseTo(1, 6);
  });
  it('380/18 短暂越过目标形成微弹，然后收敛', () => {
    expect(springProgress(0, 380, 18)).toBe(0);
    expect(springProgress(0.18, 380, 18)).toBeGreaterThan(1);
    expect(springProgress(0.18, 380, 18)).toBeLessThan(1.21);
    expect(springProgress(1, 380, 18)).toBeCloseTo(1, 3);
  });
});

describe('详情面板拖拽', () => {
  it('内容宽度直接跟手，左右移动受320–650像素边界约束', () => {
    expect(panelDragWidth(420, 1000, 960)).toBe(460);
    expect(panelDragWidth(420, 1000, 1040)).toBe(380);
    expect(panelDragWidth(420, 1000, 1500)).toBe(320);
    expect(panelDragWidth(420, 1000, 500)).toBe(650);
  });
  it('调整条暴露键盘焦点、受控面板和当前宽度', () => {
    const markup = renderToStaticMarkup(<DragHandle width={420} onWidth={() => {}} />);
    expect(markup).toContain('role="separator"');
    expect(markup).toContain('tabindex="0"');
    expect(markup).toContain('aria-controls="workspace-detail-panel"');
    expect(markup).toContain('aria-valuenow="420"');
    expect(markup).toContain('aria-valuemin="320"');
    expect(markup).toContain('aria-valuemax="650"');
  });
});
