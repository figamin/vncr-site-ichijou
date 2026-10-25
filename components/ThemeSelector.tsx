'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Moon, Sun } from 'lucide-react';

type Theme = 'light' | 'dark';
type Palette = 'current' | 'classic';

const THEMES: { value: Theme; label: string; icon: typeof Moon }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
];

const PALETTES: { value: Palette; label: string; note: string }[] = [
  { value: 'current', label: 'Modern', note: 'Amber with teal logo' },
  { value: 'classic', label: 'Drinos', note: 'The purple palette used before September' },
];

export function ThemeSelector() {
  const [mounted, setMounted] = useState(false);
  const [theme, setTheme] = useState<Theme>('light');
  const [palette, setPalette] = useState<Palette>('current');
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // The theme and the palette are two independent choices, so both are written to the root
  // together: the light/dark class the whole site reads, and the palette class that only
  // retunes the accent tokens.
  const apply = useCallback((nextTheme: Theme, nextPalette: Palette) => {
    const root = document.documentElement;
    root.classList.remove('light', 'dark');
    root.classList.add(nextTheme);
    root.classList.toggle('theme-purple', nextPalette === 'classic');

    try {
      localStorage.setItem('theme', nextTheme);
      localStorage.setItem('palette', nextPalette);
    } catch {
      // Storage can be unavailable or refused; the choice still applies to this page.
    }
  }, []);

  useEffect(() => {
    // The head script has already applied these before the first paint; reading them here
    // only brings the control's own state in line with what is on screen.
    const savedTheme = localStorage.getItem('theme');
    const savedPalette = localStorage.getItem('palette');
    const nextTheme: Theme = savedTheme === 'dark' ? 'dark' : 'light';
    const nextPalette: Palette = savedPalette === 'classic' ? 'classic' : 'current';

    setTheme(nextTheme);
    setPalette(nextPalette);
    apply(nextTheme, nextPalette);
    setMounted(true);
  }, [apply]);

  // Close on a click outside the control or on Escape.
  useEffect(() => {
    if (!isOpen) return;

    const closeOnOutside = (event: MouseEvent | TouchEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsOpen(false);
    };

    document.addEventListener('mousedown', closeOnOutside);
    document.addEventListener('touchstart', closeOnOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      document.removeEventListener('touchstart', closeOnOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [isOpen]);

  if (!mounted) {
    return (
      <button className="sw-icon" aria-label="Choose theme">
        <span className="w-5 h-5 block" />
      </button>
    );
  }

  const ThemeIcon = theme === 'light' ? Moon : Sun;

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(open => !open)}
        className="sw-icon relative"
        aria-label={`Choose theme. Currently ${theme}, ${palette === 'classic' ? 'classic purple' : 'current'} palette.`}
        aria-haspopup="menu"
        aria-expanded={isOpen}
      >
        <ThemeIcon className="w-5 h-5" aria-hidden="true" />
        <ChevronDown
          className={`w-2.5 h-2.5 transition-transform ${isOpen ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
        {palette === 'classic' && <span className="theme-dot" aria-hidden="true" />}
      </button>

      {isOpen && (
        <div role="menu" aria-label="Theme" className="sw-menu theme-menu py-1 animate-slide-down">
          <div className="sw-plate px-3 pt-2 pb-1" role="presentation">Appearance</div>
          {THEMES.map(({ value, label, icon: Icon }) => {
            const selected = theme === value;
            return (
              <button
                key={value}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                onClick={() => {
                  setTheme(value);
                  apply(value, palette);
                  setIsOpen(false);
                }}
                className={`sw-opt ${selected ? 'sw-opt--on' : ''}`}
              >
                <Icon className="w-4 h-4 shrink-0" aria-hidden="true" />
                <span>{label}</span>
                {selected && <Check className="w-3.5 h-3.5 ml-auto shrink-0" aria-hidden="true" />}
              </button>
            );
          })}

          <div className="sw-sep my-1" role="presentation" />

          <div className="sw-plate px-3 pt-2 pb-1" role="presentation">Palette</div>
          {PALETTES.map(({ value, label, note }) => {
            const selected = palette === value;
            return (
              <button
                key={value}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                onClick={() => {
                  setPalette(value);
                  apply(theme, value);
                  setIsOpen(false);
                }}
                className={`sw-opt ${selected ? 'sw-opt--on' : ''}`}
              >
                <span className={`theme-swatch theme-swatch--${value}`} aria-hidden="true" />
                <span className="min-w-0">
                  <span className="block">{label}</span>
                  <span className="sw-pick-desc block">{note}</span>
                </span>
                {selected && <Check className="w-3.5 h-3.5 ml-auto shrink-0" aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
