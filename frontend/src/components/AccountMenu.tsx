import { useState, type FormEvent } from 'react';
import { KeyRound, LogIn, LogOut, UserCircle } from 'lucide-react';
import { changePassword, getAppContext, login, logout, type AppContext } from '../api/auth';
import { setServerToken, setSessionToken } from '../api/client';

interface Props {
  context: AppContext | null;
  onContextChanged: (context: AppContext) => void;
  onAuthChanged: () => void | Promise<void>;
}

export default function AccountMenu({ context, onContextChanged, onAuthChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const user = context?.user ?? null;

  const refreshContext = async () => onContextChanged(await getAppContext());

  const doLogin = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!username.trim() || !password) return;
    setBusy(true); setMessage(''); setError('');
    try {
      const result = await login(username.trim(), password);
      setSessionToken(result.token); setServerToken(result.server_token || null); setPassword('');
      onContextChanged(result); await onAuthChanged();
      setMessage(`${result.login_mode === 'online' ? '已连接中心服务器' : '已离线登录'}：${result.user?.username || username.trim()}`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };

  const doLogout = async () => {
    setBusy(true); setMessage(''); setError('');
    try { await logout(); } catch { /* Local token removal is enough when the session has expired. */ }
    finally {
      setSessionToken(null); setServerToken(null); await refreshContext(); await onAuthChanged();
      setOldPassword(''); setNewPassword(''); setConfirmPassword(''); setMessage('已退出登录'); setBusy(false);
    }
  };

  const doChangePassword = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!oldPassword || !newPassword) return;
    if (newPassword !== confirmPassword) { setError('两次输入的新密码不一致'); return; }
    setBusy(true); setMessage(''); setError('');
    try {
      await changePassword(oldPassword, newPassword); setSessionToken(null); setServerToken(null);
      await refreshContext(); await onAuthChanged(); setOldPassword(''); setNewPassword(''); setConfirmPassword('');
      setMessage('密码已修改，请重新登录');
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };

  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((value) => !value)} className="flex items-center gap-2 px-2.5 py-1 rounded border border-gray-200 bg-white hover:border-blue-300 hover:bg-blue-50">
        <UserCircle className="w-4 h-4 text-gray-500" />
        <span className="text-xs text-gray-700">{user ? user.username : '未登录'}</span>
        {user?.role === 'admin' && <span className="text-[10px] px-1.5 py-0.5 rounded border bg-blue-50 text-blue-700 border-blue-200">admin</span>}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-80 z-50 rounded-lg border border-gray-200 bg-white shadow-lg p-4 text-sm">
          <div className="flex items-center justify-between mb-3"><div><div className="font-semibold text-gray-900">账户</div><div className="text-xs text-gray-500">{user ? `${user.username} / ${user.role}` : '中心账号登录'}</div></div></div>
          {!user ? (
            <form onSubmit={doLogin} className="space-y-2">
              <input value={username} onChange={(e) => setUsername(e.target.value)} className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="用户名" />
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="密码" />
              <button type="submit" disabled={busy || !username.trim() || !password} className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded bg-blue-600 text-white text-sm hover:bg-blue-500 disabled:opacity-50"><LogIn className="w-4 h-4" />登录</button>
            </form>
          ) : (
            <div className="space-y-4">
              <form onSubmit={doChangePassword} className="space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-medium text-gray-600"><KeyRound className="w-3.5 h-3.5" />修改密码</div>
                <input type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="旧密码" />
                <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="新密码（至少 6 位）" />
                <input type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="确认新密码" />
                <button type="submit" disabled={busy || !oldPassword || !newPassword || !confirmPassword} className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded border border-blue-200 text-blue-700 text-sm hover:bg-blue-50 disabled:opacity-50"><KeyRound className="w-4 h-4" />保存新密码</button>
              </form>
              <button type="button" disabled={busy} onClick={doLogout} className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded border border-gray-300 text-gray-700 text-sm hover:bg-gray-50 disabled:opacity-50"><LogOut className="w-4 h-4" />退出登录</button>
            </div>
          )}
          {(message || error) && <div className={`mt-3 rounded border px-3 py-2 text-xs ${error ? 'bg-red-50 border-red-200 text-red-700' : 'bg-emerald-50 border-emerald-200 text-emerald-700'}`}>{error || message}</div>}
        </div>
      )}
    </div>
  );
}
