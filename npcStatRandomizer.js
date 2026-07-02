'use strict';

/**
 * NPC Stat Randomizer
 *
 * Generates randomized stats for a batch of NPCs. Each NPC gets five basic
 * RPG-style stats rolled within a configurable range.
 */

// The five basic stats every NPC gets.
const STATS = ['strength', 'dexterity', 'constitution', 'intelligence', 'charisma'];

// Default roll range for a single stat (classic 3-18 spread).
const DEFAULT_MIN = 3;
const DEFAULT_MAX = 18;

/**
 * Return a random integer in the inclusive range [min, max].
 */
function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

/**
 * Generate a single NPC with an id and randomized stats.
 *
 * @param {number} id - Unique identifier for the NPC.
 * @param {number} min - Minimum value for each stat.
 * @param {number} max - Maximum value for each stat.
 * @returns {{id: number, strength: number, dexterity: number, constitution: number, intelligence: number, charisma: number}}
 */
function generateNpc(id, min = DEFAULT_MIN, max = DEFAULT_MAX) {
  const npc = { id };
  for (const stat of STATS) {
    npc[stat] = randomInt(min, max);
  }
  return npc;
}

/**
 * Generate an array of NPCs, each with randomized stats.
 *
 * @param {number} count - How many NPCs to generate.
 * @param {number} min - Minimum value for each stat.
 * @param {number} max - Maximum value for each stat.
 * @returns {Array<Object>} Array of NPC objects.
 */
function generateNpcs(count = 1000, min = DEFAULT_MIN, max = DEFAULT_MAX) {
  const npcs = new Array(count);
  for (let i = 0; i < count; i++) {
    npcs[i] = generateNpc(i + 1, min, max);
  }
  return npcs;
}

// Run directly with `node npcStatRandomizer.js` to print 1000 NPCs.
if (typeof require !== 'undefined' && require.main === module) {
  const npcs = generateNpcs(1000);
  console.log(`Generated ${npcs.length} NPCs. Sample:`);
  console.table(npcs.slice(0, 5));
}

// Export for Node (CommonJS); safely ignored when loaded in the browser.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { STATS, randomInt, generateNpc, generateNpcs };
}
