export const NETWORK_ERROR = "连接暂时中断，正在尝试重新连接。";
export async function api(path: string, options: RequestInit = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      ...(sessionStorage.getItem("meetflow-token")
        ? {
            Authorization: `Bearer ${sessionStorage.getItem("meetflow-token")}`,
          }
        : {}),
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  }).catch(() => {
    throw new Error(NETWORK_ERROR);
  });
  const value = await response.json().catch(() => ({}));
  if (response.status === 401) {
    sessionStorage.removeItem("meetflow-token");
    window.dispatchEvent(new Event("meetflow-session-expired"));
  }
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : "提交内容不完整，请检查后重试",
    );
  return value;
}

export const post = (path: string, body?: unknown) =>
  api(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export async function downloadFile(path: string, filename: string) {
  const response = await fetch(`/api${path}`, {
    headers: {
      Authorization: `Bearer ${sessionStorage.getItem("meetflow-token") || ""}`,
    },
  }).catch(() => {
    throw new Error(NETWORK_ERROR);
  });
  if (response.status === 401) {
    sessionStorage.removeItem("meetflow-token");
    window.dispatchEvent(new Event("meetflow-session-expired"));
  }
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(value.detail || "文件生成失败，请重试");
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 60000);
}
