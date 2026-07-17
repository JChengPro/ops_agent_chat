export type RegistrationFields = {
  username: string;
  email: string;
  password: string;
  passwordConfirmation: string;
  inviteCode: string;
};

export function validateRegistration(fields: RegistrationFields, inviteCodeRequired: boolean): string | null {
  const username = fields.username.trim();
  const email = fields.email.trim();
  if (!/^[a-z0-9][a-z0-9_.-]{2,31}$/i.test(username)) {
    return "用户名需要 3-32 个字符，只能使用字母、数字、点、下划线或连字符";
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return "请输入有效的邮箱地址";
  if (fields.password.length < 10) return "密码至少需要 10 个字符";
  if (!/[A-Za-z]/.test(fields.password) || !/\d/.test(fields.password)) return "密码必须同时包含字母和数字";
  if (fields.password !== fields.passwordConfirmation) return "两次输入的密码不一致";
  if (inviteCodeRequired && !fields.inviteCode.trim()) return "请输入管理员提供的注册码";
  return null;
}
