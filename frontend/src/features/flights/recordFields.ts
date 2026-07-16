import type { Flight, FlightRecordFields } from '../../api/flights';

export function emptyRecord(): FlightRecordFields {
  return { record_total_duration_min: null, record_location: '', record_payload: '', record_weather: '', record_fuel_amount: null, record_takeoff_weight: null, record_altitude: null, record_wind_speed: null, record_wind_direction: '', record_temperature: null, record_note: '' };
}
export function recordFromFlight(flight: Flight): FlightRecordFields {
  return { record_total_duration_min: flight.record_total_duration_min ?? null, record_location: flight.record_location ?? '', record_payload: flight.record_payload ?? '', record_weather: flight.record_weather ?? '', record_fuel_amount: flight.record_fuel_amount ?? null, record_takeoff_weight: flight.record_takeoff_weight ?? null, record_altitude: flight.record_altitude ?? null, record_wind_speed: flight.record_wind_speed ?? null, record_wind_direction: flight.record_wind_direction ?? '', record_temperature: flight.record_temperature ?? null, record_note: flight.record_note ?? '' };
}
export function parseNumberInput(value: string): number | null { if (value.trim() === '') return null; const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
export function formatDurationMinutes(value: number | null | undefined): string { if (value == null) return ''; const total = Math.max(0, Math.round(Number(value))); return `${Math.floor(total / 60)} h ${total % 60} min`; }
