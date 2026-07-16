import { useEffect, useState, type FormEvent } from 'react';
import { Check, Edit2, RefreshCw, RotateCcw, ShieldCheck, Trash2, UserPlus, Users, X } from 'lucide-react';
import {
  createUser,
  deleteUser,
  listUsers,
  resetUserPassword,
  updateUser,
} from '../api/users';
import type { CurrentUser } from '../api/auth';

function formatTime(value?: string | null) {
  if (!value) return '-';
  return value.replace('T', ' ').slice(0, 16);
}

function deleteDisabledReason(user: CurrentUser, users: CurrentUser[], currentUser: CurrentUser | null) {
  if (user.id === currentUser?.id) return '不能删除当前登录用户';
  const activeAdminCount = users.filter((item) => item.role === 'admin' && !item.disabled_at).length;
  if (user.role === 'admin' && activeAdminCount <= 1) return '不能删除最后一个管理员';
  return '';
}

export default function UserManagementPage({ currentUser }: { currentUser: CurrentUser | null }) {
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<CurrentUser['role']>('user');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [operationUserId, setOperationUserId] = useState<number | null>(null);
  const [editingUserId, setEditingUserId] = useState<number | null>(null);
  const [editingUsername, setEditingUsername] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const loadUsers = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await listUsers();
      setUsers(data.users);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadUsers();
  }, []);

  const doCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const created = await createUser(username.trim(), password, role);
      setUsername('');
      setPassword('');
      setRole('user');
      setMessage(`已创建用户：${created.username}`);
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (user: CurrentUser) => {
    setEditingUserId(user.id);
    setEditingUsername(user.username);
    setMessage('');
    setError('');
  };

  const cancelEdit = () => {
    setEditingUserId(null);
    setEditingUsername('');
  };

  const saveEdit = async (user: CurrentUser) => {
    const nextUsername = editingUsername.trim();
    if (!nextUsername || nextUsername === user.username) {
      cancelEdit();
      return;
    }
    setOperationUserId(user.id);
    setMessage('');
    setError('');
    try {
      const updated = await updateUser(user.id, nextUsername);
      setUsers((current) => current.map((item) => (item.id === user.id ? updated : item)));
      setMessage(`已更新用户名：${updated.username}`);
      cancelEdit();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOperationUserId(null);
    }
  };

  const doResetPassword = async (user: CurrentUser) => {
    if (user.id === currentUser?.id) {
      setError('不能在这里重置当前登录用户的密码');
      return;
    }
    if (!window.confirm(`将用户 "${user.username}" 的密码重置为 123456？`)) return;
    setOperationUserId(user.id);
    setMessage('');
    setError('');
    try {
      const updated = await resetUserPassword(user.id);
      setUsers((current) => current.map((item) => (item.id === user.id ? updated : item)));
      setMessage(`已将 ${updated.username} 的密码重置为 123456`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOperationUserId(null);
    }
  };

  const doDelete = async (user: CurrentUser) => {
    const reason = deleteDisabledReason(user, users, currentUser);
    if (reason) {
      setError(reason);
      return;
    }
    if (!window.confirm(`确定删除用户 "${user.username}"？`)) return;
    setOperationUserId(user.id);
    setMessage('');
    setError('');
    try {
      await deleteUser(user.id);
      setUsers((current) => current.filter((item) => item.id !== user.id));
      setMessage(`已删除用户：${user.username}`);
      if (editingUserId === user.id) cancelEdit();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOperationUserId(null);
    }
  };

  return (
    <div className="h-full overflow-auto bg-gray-50">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Users className="w-5 h-5 text-blue-600" />
              <h2 className="text-lg font-semibold text-gray-900">用户管理</h2>
            </div>
            <div className="mt-1 text-xs text-gray-500">中心服务器账号</div>
          </div>
          <button
            type="button"
            onClick={loadUsers}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 bg-white text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>

        <form onSubmit={doCreate} className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center gap-1.5 mb-3 text-sm font-medium text-gray-700">
            <UserPlus className="w-4 h-4 text-blue-600" />
            新建用户
          </div>
          <div className="grid grid-cols-1 md:grid-cols-[minmax(180px,1fr)_minmax(180px,1fr)_160px_auto] gap-3">
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="border border-gray-300 rounded px-3 py-2 text-sm"
              placeholder="用户名"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="border border-gray-300 rounded px-3 py-2 text-sm"
              placeholder="密码（至少 6 位）"
            />
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as CurrentUser['role'])}
              className="border border-gray-300 rounded px-3 py-2 text-sm bg-white"
            >
              <option value="user">普通用户</option>
              <option value="admin">管理员</option>
            </select>
            <button
              type="submit"
              disabled={saving || !username.trim() || password.length < 6}
              className="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded bg-blue-600 text-white text-sm hover:bg-blue-500 disabled:opacity-50"
            >
              <UserPlus className="w-4 h-4" />
              创建
            </button>
          </div>
          {(message || error) && (
            <div className={`mt-3 rounded border px-3 py-2 text-xs ${error ? 'bg-red-50 border-red-200 text-red-700' : 'bg-emerald-50 border-emerald-200 text-emerald-700'}`}>
              {error || message}
            </div>
          )}
        </form>

        <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
          <div className="min-w-[980px]">
            <div className="grid grid-cols-[80px_minmax(180px,1fr)_120px_160px_160px_260px] gap-3 px-4 py-2 bg-gray-100 text-xs font-medium text-gray-500">
              <div>ID</div>
              <div>用户名</div>
              <div>角色</div>
              <div>创建时间</div>
              <div>改密时间</div>
              <div>操作</div>
            </div>
            {loading ? (
              <div className="px-4 py-8 text-center text-sm text-gray-400">加载中...</div>
            ) : users.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-gray-400">暂无用户</div>
            ) : (
              users.map((user) => {
                const resetDisabledReason = user.id === currentUser?.id
                  ? '不能重置当前登录用户的密码'
                  : '';
                const deleteReason = deleteDisabledReason(user, users, currentUser);
                const isBusy = operationUserId === user.id;
                return (
                  <div
                    key={user.id}
                    className="grid grid-cols-[80px_minmax(180px,1fr)_120px_160px_160px_260px] gap-3 px-4 py-3 border-t border-gray-100 text-sm items-center"
                  >
                  <div className="text-gray-500">{user.id}</div>
                  <div className="font-medium text-gray-900 min-w-0">
                    {editingUserId === user.id ? (
                      <input
                        value={editingUsername}
                        onChange={(e) => setEditingUsername(e.target.value)}
                        className="w-full border border-blue-300 rounded px-2 py-1 text-sm"
                        autoFocus
                      />
                    ) : (
                      <div className="flex items-center gap-2 min-w-0">
                        <div className="truncate">{user.username}</div>
                        {user.id === currentUser?.id && (
                          <span className="shrink-0 px-1.5 py-0.5 rounded border border-emerald-200 bg-emerald-50 text-[10px] text-emerald-700">
                            当前
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  <div>
                    {user.role === 'admin' ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-blue-200 bg-blue-50 text-xs text-blue-700">
                        <ShieldCheck className="w-3.5 h-3.5" />
                        管理员
                      </span>
                    ) : (
                      <span className="inline-flex px-2 py-0.5 rounded border border-gray-200 bg-gray-50 text-xs text-gray-600">
                        普通用户
                      </span>
                    )}
                  </div>
                  <div className="text-gray-500">{formatTime(user.created_at)}</div>
                  <div className="text-gray-500">{formatTime(user.password_changed_at)}</div>
                  <div className="flex items-center gap-2">
                    {editingUserId === user.id ? (
                      <>
                        <button
                          type="button"
                          onClick={() => saveEdit(user)}
                          disabled={isBusy || !editingUsername.trim()}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded border border-emerald-200 text-xs text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
                        >
                          <Check className="w-3.5 h-3.5" />
                          保存
                        </button>
                        <button
                          type="button"
                          onClick={cancelEdit}
                          disabled={isBusy}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                        >
                          <X className="w-3.5 h-3.5" />
                          取消
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => startEdit(user)}
                          disabled={isBusy}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                        >
                          <Edit2 className="w-3.5 h-3.5" />
                          编辑
                        </button>
                        <button
                          type="button"
                          onClick={() => doResetPassword(user)}
                          disabled={isBusy || !!resetDisabledReason}
                          title={resetDisabledReason || '重置密码为 123456'}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded border border-amber-200 text-xs text-amber-700 hover:bg-amber-50 disabled:opacity-50"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                          重置
                        </button>
                        <button
                          type="button"
                          onClick={() => doDelete(user)}
                          disabled={isBusy || !!deleteReason}
                          title={deleteReason || '删除用户'}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded border border-red-200 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          删除
                        </button>
                      </>
                    )}
                  </div>
                </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
