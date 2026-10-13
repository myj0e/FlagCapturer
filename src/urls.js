/** URLs shown to the model or UI must not contain URL credentials or query values. */
export function publicUrl(value) {
  const url = new URL(value);
  url.username = ''; url.password = ''; url.hash = '';
  const query = [...url.searchParams].map(([name]) => [name, '[redacted]']);
  url.search = '';
  for (const [name, value] of query) url.searchParams.append(name, value);
  return url.href;
}
