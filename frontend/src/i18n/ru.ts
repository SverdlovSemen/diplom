export const ru = {
  common: {
    notAvailableShort: "нет данных",
    unknown: "неизвестно",
    ok: "ОК",
    loading: "Загрузка...",
    errorPrefix: "Ошибка:",
    loggerShort: "Логер",
    image: "Изображение",
  },
  shell: {
    appTitle: "Система считывания показаний счётчиков",
    dashboard: "Мониторинг",
    loggers: "Логеры",
    logout: "Выйти",
    roleAdmin: "админ",
    roleObserver: "наблюдатель",
    adminRequests: "Заявки на админа",
  },
  dashboard: {
    title: "Мониторинг",
    loadFailed: "Не удалось загрузить измерения",
    exportFailed: "Не удалось выгрузить CSV",
    summaryMinMaxAvg: "Мин / макс / среднее",
    cvPrefix: "CV:",
    errorShortPrefix: "ошибка:",
    observerModeHint: "Режим наблюдателя: доступны просмотр, фильтры, график и аналитика.",
    adminModeHint: "Режим администратора: доступен полный мониторинг и управление в разделе «Логеры».",
  },
  loggers: {
    title: "Логеры",
    autoRefresh: "Автообновление",
    loadFailed: "Не удалось загрузить логеры",
    createFailed: "Не удалось создать логер",
    updateFailed: "Не удалось обновить логер",
    deleteFailed: "Не удалось удалить логер",
    captureFailed: "Не удалось выполнить захват",
    bulkFailed: "Не удалось применить массовые настройки",
    applyInProgress: "Применение...",
    applyGlobally: "Применить ко всем",
    createLogger: "Создать логер",
    noLoggers: "Логеров пока нет.",
    loading: "Загрузка...",
    setup: "Настройка",
    captureNow: "Захватить сейчас",
    edit: "Изменить",
    delete: "Удалить",
    deleting: "Удаление...",
    save: "Сохранить",
    saving: "Сохранение...",
    cancel: "Отмена",
  },
} as const;

export function roleLabelRu(role: string | null | undefined): string {
  if (role === "admin") return ru.shell.roleAdmin;
  if (role === "viewer") return ru.shell.roleObserver;
  return role ?? ru.common.unknown;
}
