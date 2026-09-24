using System;

namespace MCPForUnity.Runtime.Playtest
{
    /// <summary>
    /// Exposes a static member to the playtest tools. Properties and fields are readable state;
    /// methods are callable actions whose parameters are filled from the call's args object by name.
    /// A game opts in member by member; nothing is reachable without this attribute or a
    /// <see cref="GameHooks"/> runtime registration.
    /// </summary>
    [AttributeUsage(AttributeTargets.Property | AttributeTargets.Field | AttributeTargets.Method, AllowMultiple = false)]
    public sealed class GameHookAttribute : Attribute
    {
        public string Name { get; }

        public GameHookAttribute(string name)
        {
            Name = name;
        }
    }
}
