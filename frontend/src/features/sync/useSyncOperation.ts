import { useCallback, useEffect, useState } from 'react';
import { getSyncProgress, type SyncOperationResult, type SyncProgress } from '../../api/sync';
import { operationLabel, operationMessage, type SyncActionKind } from './previewFormatters';

interface Callbacks {
  onSuccess: () => Promise<void>;
  onFailure: () => Promise<void>;
}

function createOperationId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `sync-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useSyncOperation() {
  const [busy, setBusy] = useState<SyncActionKind | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);
  const [progress, setProgress] = useState<SyncProgress | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!operationId || !busy) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await getSyncProgress(operationId);
        if (!cancelled) setProgress(data);
      } catch {
        // The first poll can race the backend before it initializes progress.
      }
    };
    poll();
    const timer = window.setInterval(poll, 500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [busy, operationId]);

  const execute = useCallback(async (
    kind: SyncActionKind,
    action: (operationId: string) => Promise<SyncOperationResult>,
    callbacks: Callbacks,
  ) => {
    const nextOperationId = createOperationId();
    setBusy(kind);
    setOperationId(nextOperationId);
    setProgress({ operation_id: nextOperationId, status: 'running', phase: '准备开始', message: `${operationLabel(kind)}正在启动`, percent: 0, created_at: '', updated_at: '' });
    setError('');
    setMessage('');
    try {
      const result = await action(nextOperationId);
      const resultMessage = operationMessage(result);
      setMessage(resultMessage);
      setProgress((prev) => prev ? { ...prev, status: result.ok ? 'completed' : 'failed', phase: result.ok ? '操作完成' : '操作失败', message: resultMessage, percent: 100 } : prev);
      await callbacks.onSuccess();
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : String(cause);
      setError(detail);
      setProgress((prev) => prev ? { ...prev, status: 'failed', phase: `${operationLabel(kind)}失败`, message: detail, percent: 100 } : prev);
      await callbacks.onFailure();
    } finally {
      setBusy(null);
    }
  }, []);

  return { busy, progress, message, error, setError, setMessage, execute };
}
