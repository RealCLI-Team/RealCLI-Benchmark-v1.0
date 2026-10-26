namespace Game.Runtime
{
    // Common parameter base carried by every buff/filter param object.
    public class Param
    {
        public int Priority;
    }

    public class AddBuffWithSoldierTypeBuff : Param
    {
        public SquadType SquadType;
        public int SoldierType; // ref:SoldierTable
    }

    public class AdjustDamageBuff : Param
    {
        public DamageElementType ElementType;
    }

    public class AdjustReceiveHealBuff : AdjustDamageBuff
    {
        public int HealAmount;
    }
}
