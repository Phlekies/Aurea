import type { Diagnostic } from '../../api/analysis';

/** User-facing names of the eight diagnostic checks. */
export const diagnosticTitles: Record<Diagnostic['code'], string> = {
  clipping: 'Saturación digital',
  hum: 'Zumbido eléctrico',
  rumble: 'Retumbo de graves',
  low_level: 'Nivel bajo',
  low_headroom: 'Margen de pico reducido',
  stationary_noise: 'Ruido continuo',
  sibilance: 'Sibilancia',
  plosives: 'Golpes de aire',
};

/** User-facing names of the registered processors. */
export const processorTitles: Record<string, string> = {
  dc_removal: 'Eliminar desplazamiento DC',
  high_pass: 'Filtro paso alto',
  dehum: 'Eliminar zumbido',
  pre_gain: 'Ajuste de nivel previo',
};
