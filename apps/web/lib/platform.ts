export interface NotificationService { scheduleReminder(input: {title:string; body:string; at:string}): Promise<void>; }
export interface LocalStore { get<T>(key:string): Promise<T | null>; set<T>(key:string, value:T): Promise<void>; }
export const browserStore: LocalStore = { async get<T>(key: string): Promise<T | null> { const raw=localStorage.getItem(key); return raw ? JSON.parse(raw) as T : null; }, async set<T>(key: string, value: T): Promise<void> { localStorage.setItem(key,JSON.stringify(value)); } };
export const noOpNotifications: NotificationService = { async scheduleReminder() { /* Native/web notification adapters arrive later. */ } };
