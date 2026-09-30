import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from './App';
import { audioConfig, jsonResponse } from './test/fixtures';

afterEach(() => vi.unstubAllGlobals());

it('shows API availability and enables upload when limits are loaded', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(jsonResponse(url === '/health' ? { status: 'ok', service: 'aurea', version: '0.3.0' } : audioConfig))));
  render(<App />);
  expect(screen.getByRole('status')).toHaveTextContent('Conectando');
  expect(await screen.findByText('Servicio conectado')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Subir una grabación/ })).toBeEnabled();
});

it('allows recovery after a connection failure', async () => {
  let healthAttempts = 0;
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
    if (url !== '/health') return Promise.resolve(jsonResponse(audioConfig));
    healthAttempts += 1;
    return healthAttempts === 1 ? Promise.reject(new TypeError('Failed to fetch')) : Promise.resolve(jsonResponse({ status: 'ok', service: 'aurea', version: '0.3.0' }));
  }));
  render(<App />);
  expect(await screen.findByText('Servicio sin conexión')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Reintentar conexión' }));
  expect(await screen.findByText('Servicio conectado')).toBeInTheDocument();
});
