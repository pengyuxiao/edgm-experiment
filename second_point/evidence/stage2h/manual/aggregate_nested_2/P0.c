#include <limits.h>
int compute(int input){
struct ag_42846fbf77_50_0_0_record { int ag_42846fbf77_50_0_0_value; };
struct ag_42846fbf77_50_0_0_other { struct ag_42846fbf77_50_0_0_record ag_42846fbf77_50_0_0_right; int ag_42846fbf77_50_0_0_input; };
struct ag_42846fbf77_50_0_0_other ag_42846fbf77_50_0_0_left = {{ input }, 0};
struct ag_42846fbf77_50_0_0_other ag_42846fbf77_50_0_0_object = {{0}, 0};
ag_42846fbf77_50_0_0_object = ag_42846fbf77_50_0_0_left;
ag_42846fbf77_50_0_0_object = ag_42846fbf77_50_0_0_left;
return (ag_42846fbf77_50_0_0_object.ag_42846fbf77_50_0_0_right.ag_42846fbf77_50_0_0_value) ^ 3;}
int main(void){int a[]={INT_MIN,INT_MAX,-1,0,1};for(int i=0;i<5;i++)if(compute(a[i])!=(a[i]^3))return 1;return 0;}
