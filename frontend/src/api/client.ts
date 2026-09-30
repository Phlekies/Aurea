/** Typed API boundary with a timeout, cancellation, and runtime payload validation. */
export interface HealthResponse {
  status: 'ok';
  service: 'aurea';
  version: string;
}

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const timeout = AbortSignal.timeout(5000);
  const response = await fetch(`${baseUrl}/health`, {
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new ApiError('No se puede conectar con Aurea.', response.status);
  const data: unknown = await response.json();
  if (
    typeof data !== 'object' || data === null ||
    !('status' in data) || data.status !== 'ok' ||
    !('service' in data) || data.service !== 'aurea' ||
    !('version' in data) || typeof data.version !== 'string'
  ) throw new ApiError('La respuesta del servicio no es válida.');
  return { status: data.status, service: data.service, version: data.version };
}
