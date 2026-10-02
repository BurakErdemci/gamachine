/** Local authentication supplies a placeholder rather than a person's name. */
export const displayName = (value?: string | null): string => {
  const name = (value ?? '').trim();
  return name.toLowerCase() === 'local' ? '' : name;
};
