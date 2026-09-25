import { AnimatePresence, motion } from 'framer-motion';
import { ChevronDown, Cpu, Hand } from 'lucide-react';
import { useRef, useState, useEffect } from 'react';
import { useLang } from '../../lib/i18n';

export type GenerationMode = 'auto' | 'step';

interface Mode {
  id: GenerationMode;
  icon: React.ReactNode;
  label: string;
  description: string;
}

interface GenerationModeSelectorProps {
  value: GenerationMode;
  onChange: (mode: GenerationMode) => void;
}

export const GenerationModeSelector = ({ value, onChange }: GenerationModeSelectorProps) => {
  const { t } = useLang();
  const MODES: Mode[] = [
    { id: 'auto', icon: <Cpu size={14} />, label: t('mode.auto'), description: t('mode.autoDesc') },
    { id: 'step', icon: <Hand size={14} />, label: t('mode.step'), description: t('mode.stepDesc') },
  ];
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = MODES.find((m: Mode) => m.id === value) ?? MODES[0];

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const autoWarning = current.id === 'auto';

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        title={autoWarning ? t('mode.autoWarning') : undefined}
        className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors ${
          autoWarning ? 'text-red-400 hover:text-red-300' : 'text-slate-500 hover:text-slate-300'
        }`}
      >
        {autoWarning && <span data-testid="auto-mode-dot" className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />}
        {current.icon}
        {current.label}
        <ChevronDown size={10} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.12 }}
            className="absolute bottom-full mb-2 left-0 w-64 bg-[#111] border border-slate-800 rounded-xl shadow-2xl overflow-hidden z-50"
          >
            <div className="px-3 pt-2.5 pb-1">
              <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider">Modes</p>
            </div>
            {MODES.map(mode => (
              <button
                key={mode.id}
                onClick={() => { onChange(mode.id); setOpen(false); }}
                className={`w-full flex items-start gap-3 px-3 py-2.5 transition-colors text-left ${
                  value === mode.id
                    ? 'bg-blue-600/15'
                    : 'hover:bg-slate-800/50'
                }`}
              >
                <span className={`mt-0.5 shrink-0 ${value === mode.id ? 'text-blue-400' : 'text-slate-500'}`}>
                  {mode.icon}
                </span>
                <div className="flex-1 min-w-0">
                  <p className={`text-[12px] font-semibold ${value === mode.id ? 'text-blue-300' : 'text-slate-300'}`}>
                    {mode.label}
                  </p>
                  <p className="text-[11px] text-slate-500 mt-0.5 leading-relaxed">
                    {mode.description}
                  </p>
                </div>
                {value === mode.id && (
                  <span className="text-blue-400 text-[11px] shrink-0 mt-0.5">✓</span>
                )}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
