/** A display name for an organization from its careers-site slug, for pages
 * whose API response doesn't carry the name (the open-roles list).
 * "zodiac-patent" -> "Zodiac Patent", "acme-corp" -> "Acme Corp". */
export function displayNameFromSlug(slug: string): string {
  const words = slug.split(/[-_]+/).filter(Boolean);
  if (words.length === 0) return "Careers";
  return words.map((word) => word[0].toUpperCase() + word.slice(1)).join(" ");
}
