// Applies the stored theme before the first paint, so a reader in dark mode never sees the
// light page flash. Served as a file and loaded without defer so it runs where it is met,
// during parsing, ahead of the body; a script rendered by the page's own components would
// only run once those components had hydrated, which is after the flash.
/* global localStorage, document */
(function () {
  try {
    const storedTheme = localStorage.getItem('theme');
    const theme = storedTheme === 'dark' ? 'dark' : 'light';
    const palette = localStorage.getItem('palette') === 'classic' ? 'classic' : 'current';
    const el = document.documentElement;
    el.classList.remove('light', 'dark', 'theme-purple');
    el.classList.add(theme, 'no-transitions');
    if (palette === 'classic') el.classList.add('theme-purple');
  } catch {
    // Storage can be unavailable or refused; the light default then stands.
  }
})();
