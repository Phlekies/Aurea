import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from './App';

afterEach(() => vi.unstubAllGlobals());

it('shows API availability and keeps future upload visibly disabled', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', service: 'aurea', version: '0.1.0' }))));
  render(<App />);
  expect(screen.getByRole('status')).toHaveTextContent('Conectando');
  expect(await screen.findByText('Servicio conectado')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Subir una grabación/ })).toBeDisabled();
});

it('allows recovery after a connection failure', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockResolvedValue(new Response(JSON.stringify({ status: 'ok', service: 'aurea', version: '0.1.0' }))));
  render(<App />);
  expect(await screen.findByText('Servicio sin conexión')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar conexión' }));
  expect(await screen.findByText('Servicio conectado')).toBeInTheDocument();
});
