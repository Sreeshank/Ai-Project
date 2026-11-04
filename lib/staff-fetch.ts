// Auth tokens live only in server-managed HttpOnly cookies.
export type StaffSession = {
  id: string;
  email: string;
  role: "admin" | "staff";
};

export async function staffFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (path.startsWith("/api/admin/") && [401, 403].includes(response.status)) {
    window.dispatchEvent(new Event("staff-session-ended"));
  }
  return response;
}

export async function staffJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await staffFetch(path, init);
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : "The staff request could not be completed.",
    );
  }
  return response.json();
}
