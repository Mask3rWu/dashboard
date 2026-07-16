import { useEffect, useState } from 'react';
import { listSubdirs, type ScanResult } from '../../api/imports';

function parseDirStructure(sourcePath: string, subdirs?: string[] | null): {
  valid: boolean;
  flightDate?: string;
  aircraftSerials?: string[];
  message: string;
  level: 'ok' | 'warn' | 'error';
} {
  if (!sourcePath.trim()) {
    return { valid: false, message: '', level: 'ok' };
  }
  // Normalize path separators
  const parts = sourcePath.replace(/\\/g, '/').split('/').filter(Boolean);

  // Find date directory (starts with 8 digits)
  let dateIdx = -1;
  for (let i = 0; i < parts.length; i++) {
    if (/^\d{8}/.test(parts[i])) {
      dateIdx = i;
      break;
    }
  }

  if (dateIdx < 0) {
    return {
      valid: false,
      message: '目录结构不符合规范：第一层目录需以 YYYYMMDD（8位日期）开头，例如 20250323_test_flight/',
      level: 'error',
    };
  }

  const dateRaw = parts[dateIdx].substring(0, 8);
  const flightDate = `${dateRaw.substring(0, 4)}-${dateRaw.substring(4, 6)}-${dateRaw.substring(6, 8)}`;

  // Use filesystem subdirectories (from API) as aircraft serials
  if (subdirs && subdirs.length > 0) {
    return {
      valid: true,
      flightDate,
      aircraftSerials: subdirs,
      message: `日期: ${flightDate}，飞机序号: ${subdirs.join(', ')}`,
      level: 'ok',
    };
  }

  // Serial from path string (if source_path goes deeper than date dir)
  const serialIdx = dateIdx + 1;
  if (serialIdx < parts.length) {
    const aircraftSerial = parts[serialIdx];
    return {
      valid: true,
      flightDate,
      aircraftSerials: [aircraftSerial],
      message: `日期: ${flightDate}，飞机序号: ${aircraftSerial}`,
      level: 'ok',
    };
  }

  // No subdirectories found on disk and no serial in path
  if (subdirs !== undefined) {
    // Filesystem was checked — truly nothing there
    return {
      valid: true,
      flightDate,
      message: `日期: ${flightDate}，未找到飞机序号子目录`,
      level: 'warn',
    };
  }

  // Still waiting for filesystem check
  return {
    valid: true,
    flightDate,
    message: `日期: ${flightDate}`,
    level: 'ok',
  };
}
export default function DirectorySummary({ sourcePath, scanResult }: { sourcePath: string; scanResult?: ScanResult | null }) {
  const [subdirs, setSubdirs] = useState<string[] | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    if (!sourcePath.trim()) {
      return;
    }
    listSubdirs(sourcePath)
      .then((data) => { if (!cancelled) setSubdirs(data.subdirs); })
      .catch(() => { if (!cancelled) setSubdirs(null); });
    return () => { cancelled = true; };
  }, [sourcePath]);

  // After scan, use actual serials from sessions as ground truth
  const scannedSerials = scanResult?.sessions
    ?.map((s) => s.aircraft_serial)
    .filter((s) => s && s.trim()) ?? [];
  const uniqueScanned = [...new Set(scannedSerials)];

  const info = uniqueScanned.length > 0
    ? parseDirStructure(sourcePath, uniqueScanned)
    : parseDirStructure(sourcePath, subdirs);

  if (!info.message) return null;

  const colors = {
    ok: 'bg-green-50 text-green-700 border-green-200',
    warn: 'bg-amber-50 text-amber-700 border-amber-200',
    error: 'bg-red-50 text-red-600 border-red-200',
  };

  return (
    <div className={`mb-3 px-3 py-2 rounded-lg border text-xs ${colors[info.level]}`}>
      {info.level === 'ok' && '✓ '}
      {info.level === 'warn' && '⚠ '}
      {info.level === 'error' && '✗ '}
      {info.message}
    </div>
  );
}
