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
