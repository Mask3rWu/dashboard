import type { FlightRecordFields } from '../../api/flights';
import { parseNumberInput } from './recordFields';

interface Props { value: FlightRecordFields; onChange: (patch: Partial<FlightRecordFields>) => void; disabled?: boolean; variant?: 'import' | 'compact'; }

export default function FlightRecordForm({ value, onChange, disabled = false, variant = 'compact' }: Props) {
  const labelClass = variant === 'import' ? 'text-[11px]' : 'text-[10px]';
  const field = (label: string, key: keyof FlightRecordFields, type: 'text' | 'number' = 'text', parse = false) => (
    <label className="space-y-1">
      <span className={`block text-gray-500 ${labelClass}`}>{label}</span>
      <input type={type} disabled={disabled} value={value[key] ?? ''} onChange={(event) => onChange({ [key]: parse ? parseNumberInput(event.target.value) : event.target.value })} className="w-full bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500" />
    </label>
  );
  const duration = value.record_total_duration_min;
  const hasDuration = duration != null && Number.isFinite(Number(duration));
  const total = hasDuration ? Math.max(0, Math.round(Number(duration))) : 0;
  const hours = hasDuration ? Math.floor(total / 60) : '';
  const minutes = hasDuration ? total % 60 : '';
  const updateDuration = (nextHours: string, nextMinutes: string) => {
    if (nextHours.trim() === '' && nextMinutes.trim() === '') return onChange({ record_total_duration_min: null });
    const h = Math.max(0, parseNumberInput(nextHours) ?? 0);
    const m = Math.max(0, parseNumberInput(nextMinutes) ?? 0);
    onChange({ record_total_duration_min: Math.round(h) * 60 + Math.round(m) });
  };
  return <>
    <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
      <label className="space-y-1"><span className={`block text-gray-500 ${labelClass}`}>总时长</span><div className="flex items-center gap-1"><input type="number" min="0" disabled={disabled} value={hours} onChange={(event) => updateDuration(event.target.value, String(minutes))} className="min-w-0 flex-1 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500" /><span className={`${labelClass} text-gray-500`}>h</span><input type="number" min="0" max="59" disabled={disabled} value={minutes} onChange={(event) => updateDuration(String(hours), event.target.value)} className="min-w-0 flex-1 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500" /><span className={`${labelClass} text-gray-500`}>min</span></div></label>
      {field('地点', 'record_location')}{field('天气', 'record_weather')}{field('设备载荷（kg）', 'record_payload', 'number')}{field('燃油量（kg）', 'record_fuel_amount', 'number', true)}{field('起飞重量（kg）', 'record_takeoff_weight', 'number', true)}{field('海拔高度（m）', 'record_altitude', 'number', true)}{field('风速（m/s）', 'record_wind_speed', 'number', true)}{field('风向', 'record_wind_direction')}{field('温度（°C）', 'record_temperature', 'number', true)}
    </div>
    <label className="space-y-1 block"><span className={`block text-gray-500 ${labelClass}`}>备注</span><textarea rows={2} disabled={disabled} value={value.record_note ?? ''} onChange={(event) => onChange({ record_note: event.target.value })} className="w-full resize-none bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500" /></label>
  </>;
}
