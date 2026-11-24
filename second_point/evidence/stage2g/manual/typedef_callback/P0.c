#include <limits.h>
struct ag_42846fbf77_50_2_0_record { int ag_42846fbf77_50_2_0_value; };
struct ag_42846fbf77_50_2_0_other { int ag_42846fbf77_50_2_0_value; };
typedef struct ag_42846fbf77_50_2_0_record ag_42846fbf77_50_2_0_root;
typedef ag_42846fbf77_50_2_0_root ag_42846fbf77_50_2_0_middle;
typedef ag_42846fbf77_50_2_0_middle ag_42846fbf77_50_2_0_argument;
typedef int (*ag_42846fbf77_50_2_0_callback)(ag_42846fbf77_50_2_0_root);
struct ag_42846fbf77_50_2_0_dispatch_type { ag_42846fbf77_50_2_0_callback ag_42846fbf77_50_2_0_invoke; };
static int ag_42846fbf77_50_2_0_extract(ag_42846fbf77_50_2_0_root ag_42846fbf77_50_2_0_input) { return ag_42846fbf77_50_2_0_input.ag_42846fbf77_50_2_0_value; }
int compute(int input){
ag_42846fbf77_50_2_0_argument ag_42846fbf77_50_2_0_object = { input };
struct ag_42846fbf77_50_2_0_dispatch_type ag_42846fbf77_50_2_0_dispatch = { ag_42846fbf77_50_2_0_extract };
return ag_42846fbf77_50_2_0_dispatch.ag_42846fbf77_50_2_0_invoke(ag_42846fbf77_50_2_0_object) ^ 3;}
int main(void){int a[]={INT_MIN,INT_MAX,-1,0,1};for(int i=0;i<5;i++)if(compute(a[i])!=(a[i]^3))return 1;return 0;}
