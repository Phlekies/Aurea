import { expect, it } from 'vitest';
import { formatNumber } from './format';

it('formats with a decimal comma and never shows a signed zero', () => {
  expect(formatNumber(-12.345)).toBe('-12,3');
  expect(formatNumber(-0.00026)).toBe('0,0');
  expect(formatNumber(-0.04, 1)).toBe('0,0');
  expect(formatNumber(-0.06, 1)).toBe('-0,1');
  expect(formatNumber(0.000004, 5)).toBe('0,00000');
  expect(formatNumber(null)).toBe('—');
});
