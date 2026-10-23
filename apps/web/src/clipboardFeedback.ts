export type CopyPhase = 'idle' | 'copying' | 'success' | 'fading' | 'error';
export const COPY_FEEDBACK_HOLD_MS = 2500;
export const COPY_FEEDBACK_FADE_MS = 160;

/** Owns clipboard completion and timers so stale writes cannot update another source panel. */
export function createCopyFeedback(onChange: (phase: CopyPhase) => void) {
  let generation = 0;
  let disposed = false;
  let hold: ReturnType<typeof setTimeout> | undefined;
  let fade: ReturnType<typeof setTimeout> | undefined;
  function clear() { clearTimeout(hold); clearTimeout(fade); }
  return {
    async copy(text: string, write: (value: string) => Promise<void>) {
      if (disposed) return;
      const attempt = ++generation;
      clear(); onChange('copying');
      try {
        await write(text);
        if (disposed || generation !== attempt) return;
        onChange('success');
        hold = setTimeout(() => {
          onChange('fading');
          fade = setTimeout(() => onChange('idle'), COPY_FEEDBACK_FADE_MS);
        }, COPY_FEEDBACK_HOLD_MS);
      } catch {
        if (!disposed && generation === attempt) onChange('error');
      }
    },
    dispose() { disposed = true; generation += 1; clear(); },
  };
}

/** Preserve the exact line endings inside the selected, version-bound snapshot range. */
export function sourceExcerpt(text: string, startLine: number, endLine: number): string {
  const lines = text.match(/[^\n]*\n|[^\n]+$/g) || [];
  const start = Math.max(0, Math.min(lines.length - 1, startLine - 1));
  const end = Math.max(start + 1, Math.min(lines.length, endLine));
  return lines.slice(start, end).join('');
}
