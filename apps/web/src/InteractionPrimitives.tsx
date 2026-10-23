import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Check, ChevronRight, Copy } from 'lucide-react';
import { createCopyFeedback, type CopyPhase } from './clipboardFeedback';
import { springProgress } from './motion';
import './interactions.css';

export function useReducedMotion() {
  const [reduced, setReduced] = useState(() => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const preference = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(preference.matches);
    update(); preference.addEventListener('change', update);
    return () => preference.removeEventListener('change', update);
  }, []);
  return reduced;
}

/** Keeps content mounted while CSS Grid animates; closed content is never focusable. */
export function Disclosure({ title, children, open, defaultOpen = false, onOpenChange, className = '' }: {
  title: ReactNode; children: ReactNode; open?: boolean; defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void; className?: string;
}) {
  const [localOpen, setLocalOpen] = useState(defaultOpen);
  const expanded = open ?? localOpen;
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!expanded && content.current?.contains(document.activeElement)) trigger.current?.focus();
  }, [expanded]);
  return <div className={`disclosure ${className}`} data-open={expanded}>
    <button className="disclosure-trigger" type="button" ref={trigger} aria-expanded={expanded} aria-controls={id}
      onClick={() => { setLocalOpen(!expanded); onOpenChange?.(!expanded); }}>
      <ChevronRight size={14} aria-hidden="true" /><span>{title}</span>
    </button>
    <div className="disclosure-grid" id={id} aria-hidden={!expanded} inert={!expanded} ref={content}>
      <div className="disclosure-clip"><div className="disclosure-content">{children}</div></div>
    </div>
  </div>;
}

const characters = new Intl.Segmenter('zh', { granularity: 'grapheme' });
export function revealCharacters(text: string): string[] { return [...characters.segment(text)].map(value => value.segment); }

/** Only newly arrived short feedback is revealed; existing analysis is always shown in full. */
export function SoftReveal({ text, fresh = false }: { text: string; fresh?: boolean }) {
  const reduced = useReducedMotion();
  const glyphs = revealCharacters(text);
  if (!fresh || reduced || glyphs.length > 48) return <span>{text}</span>;
  return <span className="soft-reveal"><span className="interaction-sr-only">{text}</span><span aria-hidden="true">{glyphs.map((glyph, index) =>
    <span key={index} className="soft-reveal-character" style={{ '--reveal-delay': `${index * 30}ms` } as CSSProperties}>{glyph}</span>)}</span></span>;
}

function SuccessMark() {
  const icon = useRef<SVGSVGElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    if (reduced || !icon.current?.animate) return;
    const animation = icon.current.animate(Array.from({ length: 31 }, (_, index) => ({
      offset: index / 30, transform: `scale(${0.76 + 0.24 * springProgress(index / 30 * 0.5, 380, 18)})`,
    })), { duration: 500, easing: 'linear' });
    return () => animation.cancel();
  }, [reduced]);
  return <Check size={14} ref={icon} aria-hidden="true" />;
}

export function CopyButton({ text, label = '复制', disabled = false }: { text: string; label?: string; disabled?: boolean }) {
  const [phase, setPhase] = useState<CopyPhase>('idle');
  const controller = useRef<ReturnType<typeof createCopyFeedback> | null>(null);
  const status = useId();
  useEffect(() => {
    const feedback = createCopyFeedback(setPhase);
    controller.current = feedback; setPhase('idle');
    return () => { feedback.dispose(); controller.current = null; };
  }, [text]);
  const success = phase === 'success' || phase === 'fading';
  return <span className="copy-action">
    <button type="button" className="copy-button" disabled={disabled || !text || phase === 'copying'}
      aria-label={label} aria-describedby={phase === 'error' ? status : undefined} title={label}
      onClick={() => void controller.current?.copy(text, value => navigator.clipboard?.writeText(value) ?? Promise.reject(new Error('clipboard unavailable')))}>
      <span className={`copy-default${success ? ' copy-hidden' : ''}`}><Copy size={14} aria-hidden="true" /><span>{phase === 'copying' ? '正在复制' : label}</span></span>
      <span className={`copy-confirmation${success ? ' copy-visible' : ''}${phase === 'fading' ? ' copy-fading' : ''}`} aria-hidden="true">{success && <SuccessMark />}<span>已复制</span></span>
    </button>
    <span id={status} className={phase === 'error' ? 'copy-error' : 'interaction-sr-only'} role="status" aria-live="polite">
      {phase === 'error' ? '复制未成功，请重试或选中文本手动复制。' : phase === 'success' ? '已复制到剪贴板' : ''}
    </span>
  </span>;
}
