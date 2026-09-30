export function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const remainder = (total % 60).toString().padStart(2, '0');
  return hours ? `${hours}:${minutes.toString().padStart(2, '0')}:${remainder}` : `${minutes}:${remainder}`;
}

export function formatSize(bytes: number): string {
  return `${(bytes / 1024 / 1024).toLocaleString('es-ES', { maximumFractionDigits: 1 })} MB`;
}

export function formatNumber(value: number | null, digits = 1): string {
  if (value === null) return '—';
  // Values that round to zero are shown as 0, never as a signed "-0,0".
  const shown = Math.abs(value) < 0.5 * 10 ** -digits ? 0 : value;
  return shown.toLocaleString('es-ES', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
